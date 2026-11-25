"""Exercise APT and dpkg against a disposable package database."""

import subprocess

import pytest
from setup_plugin_cleanup import Candidates, native

from setup_system.infrastructure.processes import run_command


def test_preview_reads_complete_native_closure_without_mutation(tmp_path, monkeypatch):
    status = tmp_path / "status"
    status.write_text(
        "Package: reportbug\nStatus: install ok installed\nArchitecture: all\n"
        "Version: 1.0\nMaintainer: Tester <a@example.invalid>\n"
        "Description: optional test package\n\n"
    )
    initial = status.read_bytes()
    args = []

    def query(*argv, check=True):
        args.append(argv)
        if argv[0] == "/usr/bin/apt-get":
            assert "--simulate" in argv
            command = [
                argv[0],
                "-o",
                "Dir::Etc::sourcelist=/dev/null",
                "-o",
                "Dir::Etc::sourceparts=-",
                "-o",
                f"Dir::State::status={status}",
                "-o",
                f"Dir::State::lists={tmp_path}",
                "-o",
                "Dir::Cache::pkgcache=",
                "-o",
                "Dir::Cache::srcpkgcache=",
                "-o",
                "Debug::NoLocking=true",
                *argv[1:],
            ]
        else:
            assert argv[0] == "/usr/bin/dpkg-query"
            command = [argv[0], "--admindir=" + str(tmp_path), *argv[1:]]
        return run_command(command, check=check, env=native.ENV)

    monkeypatch.setattr(native, "command", query)
    monkeypatch.setattr(native, "current_target", lambda: ("amd64", True))
    records = native.preview("remove", ["doc-debian", "reportbug"], 1001, tmp_path)
    assert [(p.name, p.version) for p in records] == [("reportbug", "1.0")]
    assert len(args) == 2 and status.read_bytes() == initial


@pytest.mark.parametrize(
    "mode,flag", [("remove", "--remove"), ("purge", "--purge"), ("autoremove", "--remove")]
)
def test_native_removes_only_the_approved_names(mode, flag, monkeypatch, tmp_path):
    packages = [
        native.Package(name="reportbug", architecture="all", version="1", status="installed")
    ]
    lock = tmp_path / "lock-frontend"
    lock.touch()
    monkeypatch.setattr(native, "FRONTEND_LOCK", lock)
    monkeypatch.setattr(native.os, "geteuid", lambda: 0)
    monkeypatch.setattr(native, "require_installed_resource", lambda *args: None)
    monkeypatch.setattr(native, "current_target", lambda: ("amd64", True))
    monkeypatch.setattr(native, "preview", lambda *args: packages[:1])
    monkeypatch.setattr(native, "inventory", lambda *args: [])
    commands = []
    monkeypatch.setattr(native.subprocess, "run", lambda argv, **kw: commands.append(argv))
    payload = native.Confirmation(
        mode=mode,
        selected=["reportbug"],
        caller_uid=1001,
        config_home=str(tmp_path),
        expected=native.digest(packages),
    )
    native.apply(payload)
    assert commands == [["/usr/bin/dpkg", flag, "--", "reportbug"]]
    packages[0] = packages[0].model_copy(update={"version": "2"})
    with pytest.raises(ValueError, match="changed"):
        native.apply(payload)
    assert len(commands) == 1


@pytest.mark.parametrize(
    "choice",
    ["apt", "systemd", "cryptsetup", "linux-image-amd64", "network-manager", "hyprland", "neovim"],
)
def test_protected_software_outside_catalog(choice):
    with pytest.raises(ValueError):
        Candidates(packages=[choice])


def test_editor_proof_stops_removal_before_native_change(monkeypatch, tmp_path):
    native_pkg = native.Package(name="nano", architecture="amd64", version="1", status="installed")
    monkeypatch.setattr(native, "inventory", lambda *args: [native_pkg])
    monkeypatch.setattr(native, "current_target", lambda: ("amd64", True))
    monkeypatch.setattr(
        native,
        "command",
        lambda *args, **kw: subprocess.CompletedProcess(args, 0, "Remv nano [1]\n", ""),
    )
    monkeypatch.setattr(
        native, "surviving_editor", lambda path, removed: path.name in ("nvim", "editor")
    )
    monkeypatch.setattr(
        native.pwd,
        "getpwuid",
        lambda uid: type("Account", (), {"pw_dir": str(tmp_path), "pw_name": "test"})(),
    )
    with pytest.raises(ValueError, match="independent"):
        native.preview("remove", ["nano"], 1001, tmp_path / "config")
