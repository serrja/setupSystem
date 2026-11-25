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
