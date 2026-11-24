"""Keep the shipped layer boundaries and read-only imports executable in the native gate."""

from __future__ import annotations

import ast
import asyncio
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from ansible.errors import AnsibleFilterError
from pydantic import ValidationError

from setup_system.models import (
    BaseOptions,
    PlanSnapshot,
    WorkspaceOptions,
    validate_project_settings,
)
from setup_system.runner import Target

SOURCE = Path(__file__).resolve().parents[1] / "src/setup_system"


def module_imports(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield from (node.module + "." + alias.name for alias in node.names)


def test_layer_dependencies_and_domain_purity() -> None:
    for path in SOURCE.rglob("*.py"):
        parts = path.relative_to(SOURCE).parts
        layer = parts[0]
        tree = ast.parse(path.read_text())
        imports = tuple(module_imports(tree))
        if layer in {"domain", "models.py"}:
            for name in imports:
                assert not name.startswith("setup_system.") or name.startswith(
                    "setup_system.domain."
                ), (path, name)
                assert name.split(".")[0] not in {
                    "os",
                    "subprocess",
                    "platform",
                    "socket",
                    "urllib",
                    "tempfile",
                    "shutil",
                    "importlib",
                }, (path, name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        assert node.func.id not in {"open", "__import__"}, (path, node.lineno)
                    elif isinstance(node.func, ast.Attribute):
                        assert node.func.attr not in {
                            "open",
                            "read_text",
                            "read_bytes",
                            "write_text",
                            "write_bytes",
                            "home",
                            "expanduser",
                            "resolve",
                            "exists",
                            "glob",
                            "stat",
                            "lstat",
                            "iterdir",
                            "is_file",
                            "cwd",
                        }, (path, node.lineno)
        if layer in {"infrastructure", "adapters", "bootstrap", "application"}:
            forbidden = [
                "setup_system.entrypoint.",
                "setup_system.composition.",
                "setup_system.tui.",
                "setup_system.ui.",
                "setup_system.app.",
            ]
            if layer != "application":
                forbidden.append("setup_system.application.")
            assert not any(name.startswith(tuple(forbidden)) for name in imports), path
        if layer in {"tui", "ui"}:
            assert not any(
                name.startswith(
                    (
                        "setup_system.infrastructure.",
                        "setup_system.adapters.",
                        "setup_system.bootstrap.",
                        "setup_system.composition.",
                    )
                )
                for name in imports
            ), path
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"model_construct", "model_copy"}, (path, node.lineno)


def test_imports_never_read_the_release_catalog(tmp_path: Path) -> None:
    modules = [
        ".".join(("setup_system", *path.relative_to(SOURCE).with_suffix("").parts))
        for path in sorted(SOURCE.rglob("*.py"))
        if path.name not in {"__main__.py", "__init__.py"} and "resources" not in path.parts
    ]
    program = """
import importlib, platform, sys
def observed_host_query(query):
    def call(*args, **kwargs):
        caller = sys._getframe(1).f_globals.get("__name__", "")
        if caller.startswith("setup_system."):
            raise AssertionError(f"{caller} queried the host during import")
        return query(*args, **kwargs)
    return call
for name in ("machine", "system", "freedesktop_os_release"):
    setattr(platform, name, observed_host_query(getattr(platform, name)))

def observe(event, args):
    if event == 'open' and isinstance(args[0], str):
        path = args[0]
        caller = sys._getframe(1).f_globals.get('__name__', '')
        catalog = path.endswith('.toml') and any(
            '/'+s+'/' in path for s in ('config', 'manifests', 'resources')
        )
        private = path.startswith(('/etc/', sys.argv[2] + '/'))
        if catalog or (private and caller.startswith('setup_system.')):
            raise AssertionError('Import opened ' + path)
sys.addaudithook(observe)
sys.path.insert(0, sys.argv[1])
for module in sys.argv[3:]:
    importlib.import_module(module)
"""
    subprocess.run(
        [sys.executable, "-I", "-B", "-c", program, str(SOURCE.parent), str(tmp_path), *modules],
        cwd=tmp_path,
        env={**os.environ, "HOME": str(tmp_path)},
        check=True,
        timeout=45,
    )


def test_confirmed_plan_detaches_and_revalidates_nested_inputs() -> None:
    choices = ["neovim"]
    actions = ["Configure Neovim"]
    observed = {"needs_user": True, "needs_system": False, "closure": ["neovim"]}
    reply = {"status": "ready", "actions": actions, "observed": observed}
    plan = PlanSnapshot.capture("workspace", {"apps": choices}, reply, "revision")
    choices[:] = ["firefox"]
    actions.clear()
    observed["closure"].clear()
    payload = plan.payload()
    assert payload["settings"] == {"apps": ["neovim"]}
    assert payload["expected"]["actions"] == ["Configure Neovim"]
    assert payload["expected"]["observed"]["closure"] == ["neovim"]
    payload["settings"]["apps"].clear()
    assert plan.payload()["settings"] == {"apps": ["neovim"]}
    fields = plan.model_dump()
    for invalid in (
        {"task": "unknown"},
        {"expected_json": json.dumps({"status": "ready", "actions": [{"requires": "missing"}]})},
        {"settings_json": json.dumps({"apps": [{"executable": "/bin/false"}]})},
        {"matched": 1},
    ):
        with pytest.raises(ValidationError):
            PlanSnapshot.model_validate(fields | invalid)


@pytest.mark.parametrize(
    ("section", "settings"),
    [
        ("base", {"hyprland": "yes"}),
        ("base", {"schema_version": True}),
        ("base", {"timezone": "../outside"}),
        ("base", {"keyboard": "UTC"}),
        ("base", {"timezone": "UTC", "closure": ["nano"]}),
        ("workspace", {"packages": ["git"], "expected_happ_version": "1.0"}),
        ("workspace", {"packages": []}),
        ("workspace", {"theme": {"effects": "off"}}),
    ],
)
def test_user_and_native_command_schema_reject_invalid_variants(section, settings):
    request = {"schema": 1, "settings": settings, "expected": None}
    validate = runpy.run_path(str(SOURCE.parents[1] / "ansible/filter_plugins/native.py"))[
        "validate_request"
    ]
    model = BaseOptions if section == "base" else WorkspaceOptions
    with pytest.raises(ValidationError):
        model.model_validate(settings)
    with pytest.raises((AnsibleFilterError, ValueError)):
        validate(json.loads(json.dumps(request)), section, {}, {})
    with pytest.raises(ValueError):
        Target("temporary_network")
    with pytest.raises((AnsibleFilterError, ValueError)):
        validate(request | {"settings": {}}, "unknown", {}, {})


def test_ui_and_apply_read_the_same_settings_at_the_bound_paths(tmp_path, monkeypatch):
    from textual.widgets import Input

    from setup_system.app import SetupApp
    from setup_system.settings import Settings

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    bound = Settings()
    app = SetupApp(settings=bound)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "other"))
    base = bound.save("base", BaseOptions(timezone="UTC"), bound.load("base").revision)
    user = bound.save(
        "workspace", WorkspaceOptions(apps=("firefox",)), bound.load("workspace").revision
    )

    async def exercise():
        async with app.run_test(size=(100, 35)):
            assert app.loaded == {"base": base, "workspace": user}
            assert app.query_one("#timezone", Input).value == "UTC"
            current = bound.directory / "base.toml"
            current.write_text('schema_version=1\ntimezone="Asia/Tokyo"\n')
            with pytest.raises(ValueError, match="changed"):
                app.verify_revision("base", base.revision)
            current.chmod(0o666)
            app.reload("base")
            assert "base" not in app.loaded and app.loaded["workspace"] == user
            with pytest.raises(ValueError, match="Unsafe"):
                app.verify_revision("base", base.revision)

    asyncio.run(exercise())
    assert not (tmp_path / "other").exists()


def test_one_platform_policy_covers_base_and_workspace_plans(tmp_path, monkeypatch):
    from setup_system.infrastructure.native_target import current_target, target

    root = SOURCE.parents[1]
    for section in ("base", "workspace"):
        (play,) = yaml.safe_load((root / f"ansible/{section}.yml").read_text())
        assert play["vars"]["section"] == section
        assert "tasks/run.yml" in str(play["tasks"])
    run = yaml.safe_load((root / "ansible/tasks/run.yml").read_text())
    assert str(run).count("/files/facts.py") == 1
    facts = runpy.run_path(str(root / "ansible/files/facts.py"))
    assert facts["current_target"] is current_target
    release = {"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"}
    assert target(release, "aarch64", "arm64") == ("arm64", True)
    assert target(release | {"VERSION_CODENAME": "bookworm"}, "aarch64", "arm64") == (
        "arm64",
        False,
    )
    assert target(release, "mips", "mips")[1] is False
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("task", "bad"),
    [
        ("template-cache", {"template": "python", "offline": "false"}),
        ("template-update", {"template": "python", "destination": "/work", "project_name": "new"}),
        ("template-create", {"template": "python", "destination": "/work/../outside"}),
        ("git-inspect", {"repository": "not-absolute"}),
        ("git-use", {"repository": "/work", "name": "team", "owner_directory": "/other"}),
        ("rtk-install", {"binary": "/other/rtk"}),
        ("project-editor", {"project_id": "team/name", "command": "/other/nvim"}),
        ("project-list", {"shell": "/bin/sh"}),
    ],
)
def test_project_operations_reject_unrelated_fields_and_paths(task, bad):
    with pytest.raises(ValidationError):
        validate_project_settings(task, bad)


def test_project_plan_is_user_only_and_detaches_exact_requested_destination():
    settings = {"template": "python", "destination": "/work/Тест 2", "project_name": "example"}
    reply = {
        "status": "ready",
        "actions": ["Create selected checkout"],
        "observed": {"commit": "old"},
    }
    plan = PlanSnapshot.capture("template-create", settings, reply, None)
    settings["destination"] = "/work/next"
    reply["observed"]["commit"] = "changed"
    assert not plan.needs_password
    assert plan.payload()["settings"]["destination"] == "/work/Тест 2"
    assert plan.payload()["expected"]["observed"] == {"commit": "old"}
    assert validate_project_settings("template-cache", {"template": "python"}) == {
        "template": "python",
        "offline": False,
    }
    with pytest.raises(ValidationError):
        PlanSnapshot.capture("project-editor", {"project_id": "team/name"}, reply, None)
