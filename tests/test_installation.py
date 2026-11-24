"""One-command boundaries with synthetic accounts, paths and package executors."""

from __future__ import annotations

import os
import pwd
import subprocess
import sys
from pathlib import Path

import pytest

from setup_system.domain import identity as account_identity
from setup_system.infrastructure import identity, processes, service_policy, system_files

ROOT = Path(__file__).resolve().parents[1]
RELEASE = {"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"}


def account(home: Path, uid: int = 1001) -> pwd.struct_passwd:
    return pwd.struct_passwd(("alice", "x", uid, 1001, "", str(home), "/bin/bash"))


def test_sudo_account_uses_passwd_home_not_root_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home with spaces"
    home.mkdir()
    monkeypatch.setattr(identity.pwd, "getpwnam", lambda name: account(home))
    monkeypatch.setattr(identity.grp, "getgrgid", lambda gid: object())
    monkeypatch.setattr(identity, "validate_home", lambda target: None)
    target = identity.resolve_target(
        None,
        environment={"SUDO_USER": "alice", "SUDO_UID": "1001", "SUDO_GID": "1001", "HOME": "/root"},
        effective_uid=0,
        real_uid=0,
    )
    assert target.home == home and target.uid == 1001
    env = identity.user_environment(target, {"config": home / "config with spaces"})
    assert env["HOME"] == str(home) and env["XDG_CONFIG_HOME"].endswith("config with spaces")
    assert (
        not {"SUDO_USER", "DBUS_SESSION_BUS_ADDRESS", "SSH_AUTH_SOCK", "GH_TOKEN", "PYTHONPATH"}
        & env.keys()
    )


@pytest.mark.parametrize("case", ["root", "missing", "uid", "gid", "unknown", "zero"])
def test_invalid_identity_fails_before_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    def lookup(name: str) -> pwd.struct_passwd:
        if case == "unknown":
            raise KeyError(name)
        return account(tmp_path, 0 if case == "zero" else 1001)

    monkeypatch.setattr(identity.pwd, "getpwnam", lookup)
    monkeypatch.setattr(identity.grp, "getgrgid", lambda gid: object())
    environment = {"SUDO_USER": "alice", "SUDO_UID": "1001", "SUDO_GID": "1001"}
    if case == "root":
        environment = {}
    if case == "missing":
        environment.pop("SUDO_UID")
    if case == "uid":
        environment["SUDO_UID"] = "1002"
    if case == "gid":
        environment["SUDO_GID"] = "1002"
    with pytest.raises(ValueError):
        identity.resolve_target(None, environment=environment, effective_uid=0, real_uid=0)


@pytest.mark.parametrize(
    "suffix", ["../outside", "config/../../root", ".config/../other", "bad\npath"]
)
def test_unsafe_user_paths_rejected(tmp_path: Path, suffix: str) -> None:
    target = account_identity.TargetUser("alice", os.getuid(), os.getgid(), tmp_path, "/bin/bash")
    with pytest.raises(ValueError):
        identity.validate_user_path(tmp_path / suffix, target)


def test_symlink_path_rejected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "config").symlink_to(tmp_path)
    target = account_identity.TargetUser("alice", os.getuid(), os.getgid(), home, "/bin/bash")
    with pytest.raises(ValueError):
        identity.validate_user_path(home / "config/new", target)


@pytest.mark.parametrize("mask", [0o002, 0o077, 0o777])
def test_system_journal_parents_are_private_independently_of_umask(
    tmp_path: Path, mask: int
) -> None:
    destination = tmp_path / "new-system-state" / "nested" / "install.json"
    previous = os.umask(mask)
    try:
        system_files.write_system(destination, b"first\n")
        system_files.write_system(destination, b"second\n")
    finally:
        os.umask(previous)
    assert destination.read_bytes() == b"second\n"
    assert destination.stat().st_mode & 0o777 == 0o600
    for directory in (destination.parent, destination.parent.parent):
        assert directory.stat().st_mode & 0o777 == 0o700


def test_unknown_service_policy_stops_before_any_mutation(tmp_path):
    path = tmp_path / "policy-rc.d"
    path.write_bytes(b"#!/bin/sh\nexit 0\n")
    path.chmod(0o600)
    with pytest.raises(ValueError, match="service policy"):
        with service_policy.defer_services(path):
            pytest.fail("Unknown service policy admitted a package transaction")
    assert path.read_bytes().endswith(b"exit 0\n")


def test_interrupted_owned_service_policy_is_removed_on_retry(tmp_path: Path) -> None:
    path = tmp_path / "policy"
    path.write_bytes(service_policy.DEFER_POLICY)
    path.chmod(0o600)
    with service_policy.defer_services(path):
        assert path.read_bytes() == service_policy.DEFER_POLICY
    assert not path.exists()


def test_group_writable_owned_service_policy_is_preserved_and_rejected(tmp_path: Path) -> None:
    path = tmp_path / "policy"
    path.write_bytes(service_policy.DEFER_POLICY)
    path.chmod(0o664)
    with pytest.raises(ValueError, match="privately controlled"):
        with service_policy.defer_services(path):
            pytest.fail("Unsafe policy reached the package stage")
    assert path.read_bytes() == service_policy.DEFER_POLICY
    assert path.stat().st_mode & 0o777 == 0o664


def _wait_for_leaf_ready(monkeypatch: pytest.MonkeyPatch, pid_file: Path) -> None:
    """Start the timeout clock only after the real TERM-ignoring leaf is ready."""
    import signal
    import time

    real_popen = subprocess.Popen

    def ready_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        deadline = time.monotonic() + 20
        while not pid_file.exists():
            if process.poll() is not None or time.monotonic() >= deadline:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate(timeout=5)
                pytest.fail("Real descendant did not reach readiness before timeout test")
            time.sleep(0.01)
        return process

    monkeypatch.setattr(subprocess, "Popen", ready_popen)


def test_timeout_terminates_unprivileged_descendant_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import signal
    import time

    pid_file = tmp_path / "child.pid"
    leaf = (
        "import os,signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "Path(" + repr(str(pid_file)) + ").write_text(str(os.getpid())); time.sleep(60)"
    )
    code = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable, '-c', " + repr(leaf) + "], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); time.sleep(60)"
    )
    _wait_for_leaf_ready(monkeypatch, pid_file)
    with pytest.raises(subprocess.TimeoutExpired):
        processes.run_command([sys.executable, "-c", code], timeout=1, input="fixture input")
    child = int(pid_file.read_text())
    try:
        for _ in range(20):
            status = Path(f"/proc/{child}/stat")
            if not status.exists() or status.read_text().split(") ", 1)[1].startswith("Z "):
                break
            time.sleep(0.05)
        else:
            pytest.fail("Child survived installer process-group timeout")
    finally:
        try:
            os.kill(child, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_nonexecutable_administrator_policy_stops_before_mutation(tmp_path):
    path = tmp_path / "policy-rc.d"
    path.write_bytes(b"#!/bin/sh\nexit 101\n")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="executable"):
        with service_policy.defer_services(path):
            pytest.fail("Inactive administrator policy reached a package transaction")
    assert path.stat().st_mode & 0o777 == 0o644


def test_new_service_deferral_is_executable_with_restrictive_umask(tmp_path: Path) -> None:
    path = tmp_path / "policy"
    previous = os.umask(0o777)
    try:
        with service_policy.defer_services(path):
            assert path.stat().st_mode & 0o100
    finally:
        os.umask(previous)
    assert not path.exists()


def test_owned_interrupted_policy_repairs_executable_mode_before_use(tmp_path: Path) -> None:
    path = tmp_path / "policy"
    path.write_bytes(service_policy.DEFER_POLICY)
    path.chmod(0o644)
    with service_policy.defer_services(path):
        assert path.stat().st_mode & 0o100
    assert not path.exists()


def test_privileged_existing_file_requires_controlled_parent(tmp_path: Path) -> None:
    parent = tmp_path / "shared-parent"
    parent.mkdir()
    parent.chmod(0o777)
    path = parent / "system-file"
    path.write_bytes(b"original")
    path.chmod(0o600)
    with pytest.raises(ValueError, match="privately controlled"):
        system_files.write_system(path, b"replacement")
    assert path.read_bytes() == b"original"
