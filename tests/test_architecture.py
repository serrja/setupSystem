"""Keep the shipped layer boundaries and read-only imports executable in the native gate."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from setup_system.domain.operations import InstallRequest, Operation, WorkstationPlan
from setup_system.domain.settings import Section, SystemSettings, UserSettings
from setup_system.domain.system_commands import COMMAND

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
        if layer == "domain":
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
            ]
            if layer != "application":
                forbidden.append("setup_system.application.")
            assert not any(name.startswith(tuple(forbidden)) for name in imports), path
        if layer == "tui":
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
        if path.endswith('.toml') and ('/manifests/' in path or '/resources/' in path):
            raise AssertionError('Import opened ' + path)
sys.addaudithook(observe)
sys.path.insert(0, sys.argv[1])
for module in sys.argv[2:]:
    importlib.import_module(module)
"""
    subprocess.run(
        [sys.executable, "-I", "-B", "-c", program, str(SOURCE.parent), *modules],
        cwd=tmp_path,
        env={**os.environ, "HOME": str(tmp_path)},
        check=True,
        timeout=45,
    )


def test_confirmed_plan_detaches_and_revalidates_nested_inputs() -> None:
    choices = ["neovim"]
    request = InstallRequest.model_validate(
        {"operation": "apply", "section": "workspace", "settings": {"apps": choices}}
    )
    raw = {"kind": "application", "id": "neovim", "description": "Configure selected editor"}
    plan = WorkstationPlan.model_validate(
        {
            "section": request.section,
            "system": request.system,
            "user": request.settings,
            "actions": [raw],
        }
    )
    raw["id"] = "firefox"
    choices[:] = ["codex"]
    next_request = request.validated(
        operation=Operation.PLAN, settings=UserSettings(apps=("firefox",))
    )
    assert next_request.settings.apps == ("firefox",)
    assert plan.user.apps == ("neovim",) and plan.actions[0].id == "neovim"
    with pytest.raises(ValidationError):
        plan.validated(section="unknown")
    with pytest.raises(ValidationError):
        plan.validated(actions=[{**raw, "requires": ["missing"]}])
    with pytest.raises(ValidationError):
        WorkstationPlan(
            section=Section.BASE,
            system=SystemSettings(),
            user=UserSettings(),
            actions=(),
            matched=1,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "action": "base",
            "settings": {
                "hyprland": True,
                "graphical_login": False,
                "plymouth": False,
                "schema_version": True,
            },
        },
        {
            "action": "login",
            "settings": {
                "hyprland": True,
                "graphical_login": False,
                "plymouth": False,
                "schema_version": 1,
            },
        },
        {
            "action": "workspace_packages",
            "settings": {"packages": ["git"], "expected_happ_version": "1.0"},
        },
        {"action": "timezone", "settings": {"value": "UTC", "closure": ["nano"]}},
        {"action": "temporary_network", "settings": {}},
        {"action": "timezone", "settings": {"value": "../outside"}},
        {"action": "keyboard", "settings": {"value": "UTC"}},
        {"action": "workspace_packages", "settings": {"packages": []}},
    ],
)
def test_user_and_privileged_command_schema_reject_invalid_variants(payload):
    import json

    with pytest.raises(ValidationError):
        COMMAND.validate_python(payload)
    with pytest.raises(ValidationError):
        COMMAND.validate_json(json.dumps(payload))
