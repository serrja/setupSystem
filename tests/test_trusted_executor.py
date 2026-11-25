"""Installed Ansible resources delimit the system privilege boundary."""

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "plugin, action",
    [("administration", "grant-sudo"), ("cleanup", "remove"), ("greeter", "activate-login")],
)
def test_system_apply_requires_the_installed_package(tmp_path, monkeypatch, plugin, action):
    from setup_system_sdk import native_play

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr(os, "isatty", lambda fd: True)
    monkeypatch.setenv("ANSIBLE_CONFIG", str(tmp_path / "foreign.cfg"))
    monkeypatch.setenv("SETUP_SYSTEM_INTERPRETER", str(tmp_path / "foreign"))
    monkeypatch.setattr(
        native_play.subprocess, "run", lambda *a, **k: pytest.fail("untrusted recipe")
    )
    with pytest.raises(ValueError, match="protected resource location"):
        native_play.main(plugin, action)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "variant",
    [
        "shared_stage",
        "shared_request",
        "symlink_request",
        "unexpected_result",
        "hardlink",
        "existing_result",
        "safe",
    ],
)
def test_private_request_and_result_share_the_verified_directory(tmp_path, monkeypatch, variant):
    from setup_system_sdk import Mutation, Plan
    from setup_system_sdk.native_play import private_exchange

    stage = tmp_path / "operation"
    stage.mkdir(mode=0o700)
    request = stage / "request.json"
    expected = Mutation(action_id="verified", settings={}, inputs={}, plan=Plan(summary="fixture"))
    request.write_text(expected.model_dump_json())
    request.chmod(0o600)
    result = stage / "result.json"
    if variant == "shared_stage":
        stage.chmod(0o750)
    elif variant == "shared_request":
        request.chmod(0o640)
    elif variant == "symlink_request":
        target = stage / "target"
        request.rename(target)
        request.symlink_to(target)
    elif variant == "hardlink":
        os.link(request, stage / "second")
    elif variant == "unexpected_result":
        result = tmp_path / result.name
    elif variant == "existing_result":
        result.touch()
    monkeypatch.setenv("SETUP_SYSTEM_REQUEST", str(request))
    monkeypatch.setenv("SETUP_SYSTEM_RESULT", str(result))
    if variant == "safe":
        with private_exchange() as (actual, descriptor):
            assert actual == expected and os.fstat(descriptor).st_ino == stage.stat().st_ino
        return
    with pytest.raises((ValueError, OSError)), private_exchange():
        pytest.fail("admitted unsafe staging")
    if variant != "existing_result":
        assert not result.exists()


def test_authorization_refuses_unregistered_recipe_and_wrong_package(tmp_path, monkeypatch):
    from setup_system import layout

    root = tmp_path / "payload"
    python_package = tmp_path / "python/setup_system"
    root.mkdir()
    python_package.mkdir(parents=True)
    makefile = root / "Makefile"
    makefile.touch()
    (root / "extra.mk").touch()
    monkeypatch.setattr(layout, "INSTALLED", root)
    monkeypatch.setattr(layout, "PYTHON_PACKAGE", python_package)
    registered = {root, makefile, python_package}
    monkeypatch.setattr(layout, "_package_paths", lambda: registered)
    monkeypatch.setattr(layout, "protected", lambda path, *, directory=True: None)
    with pytest.raises(ValueError, match="package inventory"):
        layout.verify_installation(root)
    with pytest.raises(ValueError, match="installed Debian"):
        layout.verify_installation(tmp_path / "checkout")


def test_package_inventory_requires_configured_unmodified_package(monkeypatch):
    from setup_system import layout

    monkeypatch.setattr(layout, "_query", lambda *args: "deinstall ok config-files")
    with pytest.raises(ValueError, match="configured"):
        layout._package_paths()

    def modified(binary, *args):
        if binary == "/usr/bin/dpkg":
            return "??5??????  /usr/lib/setup-system/Makefile"
        return "install ok installed" if "-W" in args else "/usr/lib/setup-system"

    monkeypatch.setattr(layout, "_query", modified)
    with pytest.raises(ValueError, match="integrity"):
        layout._package_paths()


def test_held_installed_package_uses_its_registered_resources(monkeypatch):
    from setup_system import layout

    def held(binary, *args):
        if binary == "/usr/bin/dpkg":
            return ""
        if "-W" in args:
            return "hold ok installed"
        return str(layout.INSTALLED)

    monkeypatch.setattr(layout, "_query", held)
    assert layout._package_paths() == {layout.INSTALLED}


@pytest.mark.parametrize(
    "variant", ["complete", "unapproved-target", "changed-plan", "lost-result"]
)
def test_installed_native_dispatch_validates_manifest_and_reads_back_private_result(
    tmp_path, monkeypatch, variant
):
    import json
    import stat
    import sys

    from setup_system_sdk import (
        Inputs,
        Manifest,
        Mutation,
        Outcome,
        Plan,
        PluginSettings,
        Result,
        native_play,
    )

    installed = tmp_path / "installed"
    folder = installed / "plugins/fixture/setup_plugin_fixture"
    folder.mkdir(parents=True)
    declaration = {
        "id": "fixture",
        "name": "Fixture",
        "category": "system",
        "author": "tests",
        "kind": "system",
        "version": "1.0",
        "source": "https://example.invalid",
        "sdk": ">=1,<2",
        "entry_point": "setup_plugin_fixture:Plugin",
        "capabilities": [],
        "dependencies": [],
        "permissions": ["root"],
        "settings_schema": PluginSettings.model_json_schema(),
        "input_schemas": {"configure": Inputs.model_json_schema()},
        "actions": [
            {
                "id": "configure",
                "label": "Configure",
                "target": "configure",
                "permissions": [] if variant == "unapproved-target" else ["root"],
            }
        ],
    }
    (folder / "manifest.json").write_text(Manifest.model_validate(declaration).model_dump_json())
    (folder / "__init__.py").write_text(
        "from pathlib import Path\n"
        "from setup_system_sdk import Backend, Inputs, Plan, Outcome, Result\n"
        "class Plugin(Backend):\n"
        '    inputs = {"configure": Inputs}\n'
        "    async def plan(self, action, settings, inputs):\n"
        '        return Plan(summary="Publish fixture")\n'
        "    async def verify(self, plan, settings, inputs):\n"
        '        value=Path("value").read_text()\n'
        "        return Result(outcome=Outcome.COMPLETE, message=value)\n"
        "def apply_native(action, settings, inputs, plan):\n"
        '    Path("value").write_text("observed fixture")\n'
        + ('    raise OSError("lost reply")\n' if variant == "lost-result" else "")
    )
    private = tmp_path / "operation"
    private.mkdir(mode=0o700)
    request, result = private / "request.json", private / "result.json"
    request.write_text(
        Mutation(
            action_id="configure",
            inputs={},
            settings={},
            plan=Plan(summary="stale" if variant == "changed-plan" else "Publish fixture"),
        ).model_dump_json()
    )
    request.chmod(0o600)
    monkeypatch.setenv("SETUP_SYSTEM_REQUEST", str(request))
    monkeypatch.setenv("SETUP_SYSTEM_RESULT", str(result))
    monkeypatch.setattr(native_play, "INSTALLED", installed)
    monkeypatch.setattr(os, "isatty", lambda fd: fd == 0)
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.chdir(folder)
    checked = []

    def verify_tree(source, relative):
        assert source == installed / relative and source == folder / "__init__.py"
        checked.append(True)

    monkeypatch.setattr(native_play, "require_installed_resource", verify_tree)
    monkeypatch.setitem(sys.modules, "setup_plugin_fixture", None)
    if variant == "unapproved-target":
        with pytest.raises(ValueError, match="approved privileged target"):
            native_play.main("fixture", "configure")
        assert not result.exists() and not (folder / "value").exists()
        return
    native_play.main("fixture", "configure")
    assert checked == [True]
    returned = Result.model_validate(json.loads(result.read_text()))
    assert stat.S_IMODE(result.stat().st_mode) == 0o600
    outcomes = {
        "complete": Outcome.COMPLETE,
        "changed-plan": Outcome.BLOCKED,
        "lost-result": Outcome.UNKNOWN_OUTCOME,
    }
    assert returned.outcome == outcomes[variant]
    assert (folder / "value").exists() == (variant != "changed-plan")
    if variant == "complete":
        assert returned.message == "observed fixture"


@pytest.mark.parametrize("playbook,method", [("configure.yml", "su"), ("@apt", "sudo")])
def test_native_play_uses_only_system_terminal_and_registered_payload(
    tmp_path, monkeypatch, playbook, method
):
    import base64
    import json

    from setup_system_sdk import native_play

    installed = tmp_path / "installed"
    folder = installed / "plugins/fixture/setup_plugin_fixture"
    folder.mkdir(parents=True)
    monkeypatch.chdir(folder)
    monkeypatch.setattr(native_play, "INSTALLED", installed)
    monkeypatch.setattr(os, "isatty", lambda fd: fd == 0)
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    for name in (
        "ANSIBLE_CONFIG",
        "ANSIBLE_COLLECTIONS_PATH",
        "MAKEFLAGS",
        "PYTHONPATH",
        "LD_PRELOAD",
    ):
        monkeypatch.setenv(name, "synthetic-untrusted-option")
    checked = []

    def require(source, relative):
        assert source == installed / relative
        assert relative == (
            "sdk/apt.yml"
            if playbook == "@apt"
            else "plugins/fixture/setup_plugin_fixture/configure.yml"
        )
        checked.append(source)

    monkeypatch.setattr(native_play, "require_installed_resource", require)
    public = {"setting": "A literal string with spaces and Пример"}
    calls = []

    def ansible(argv, *, cwd, env, check):
        assert check and cwd == installed and len(checked) == 1
        assert argv[:5] == ["/usr/bin/python3", "-I", "-B", "/usr/bin/ansible-playbook", "-i"]
        assert argv[argv.index("--become-method") + 1] == method and "--ask-become-pass" in argv
        assert Path(argv[-1]) == checked[0]
        assert all("synthetic-untrusted-option" not in value for value in env.values())
        assert env["ANSIBLE_CONFIG"] == str(installed / "ansible.cfg")
        assert Path(env["HOME"]).is_dir()
        payload = argv[argv.index("--extra-vars") + 1].removeprefix("setup_system_payload=")
        assert json.loads(base64.b64decode(payload)) == public
        calls.append(Path(env["HOME"]))

    monkeypatch.setattr(native_play.subprocess, "run", ansible)
    native_play.run_native_play("fixture", playbook, public, method)
    assert len(calls) == 1 and not calls[0].exists()
