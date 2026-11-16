"""One-command boundaries with synthetic accounts, paths and package executors."""

from __future__ import annotations

import os
import pwd
import subprocess
from pathlib import Path

import pytest

from setup_system import installation as install

ROOT = Path(__file__).resolve().parents[1]
RELEASE = {"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"}


def account(home: Path, uid: int = 1001) -> pwd.struct_passwd:
    return pwd.struct_passwd(("alice", "x", uid, 1001, "", str(home), "/bin/bash"))


def test_sudo_account_uses_passwd_home_not_root_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home with spaces"
    home.mkdir()
    monkeypatch.setattr(install.pwd, "getpwnam", lambda name: account(home))
    monkeypatch.setattr(install.grp, "getgrgid", lambda gid: object())
    monkeypatch.setattr(install, "validate_home", lambda target: None)
    target = install.resolve_target(
        None,
        environment={"SUDO_USER": "alice", "SUDO_UID": "1001", "SUDO_GID": "1001", "HOME": "/root"},
        effective_uid=0,
        real_uid=0,
    )
    assert target.home == home and target.uid == 1001
    env = install.user_environment(target, {"config": home / "config with spaces"})
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

    monkeypatch.setattr(install.pwd, "getpwnam", lookup)
    monkeypatch.setattr(install.grp, "getgrgid", lambda gid: object())
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
        install.resolve_target(None, environment=environment, effective_uid=0, real_uid=0)


@pytest.mark.parametrize(
    "suffix", ["../outside", "config/../../root", ".config/../other", "bad\npath"]
)
def test_unsafe_user_paths_rejected(tmp_path: Path, suffix: str) -> None:
    target = install.TargetUser("alice", os.getuid(), os.getgid(), tmp_path, "/bin/bash")
    with pytest.raises(ValueError):
        install.validate_user_path(tmp_path / suffix, target)


def test_symlink_path_rejected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "config").symlink_to(tmp_path)
    target = install.TargetUser("alice", os.getuid(), os.getgid(), home, "/bin/bash")
    with pytest.raises(ValueError):
        install.validate_user_path(home / "config/new", target)


def test_apt_simulation_refuses_removal_and_downgrade() -> None:
    for text in ("Remv gnome-shell [1.0]", "The following packages will be DOWNGRADED:\n hyprland"):
        with pytest.raises(RuntimeError):
            install.validate_simulation(text)
    install.validate_simulation("Inst hyprland (0.55.2 Debian Backports)\nConf hyprland")


@pytest.mark.parametrize(
    "case", ["offline", "signature", "missing-package", "apt-lock", "timeout", "interrupted"]
)
def test_system_failure_records_actual_stage_and_never_user_success(
    tmp_path: Path, case: str
) -> None:
    paths = install.SystemPaths(
        tmp_path / "etc/setup-system.sources",
        tmp_path / "state/install.json",
        tmp_path / "keyring.gpg",
        tmp_path / "run",
        tmp_path / "policy-rc.d",
    )
    paths.keyring.write_bytes(b"test trust anchor")
    paths.runtime.mkdir()
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        package_apply = "install" in argv and "--simulate" not in argv
        if case == "timeout" and package_apply:
            raise subprocess.TimeoutExpired(argv, 1)
        if case == "interrupted" and package_apply:
            raise KeyboardInterrupt
        if (
            (case in {"offline", "signature"} and "update" in argv)
            or (case == "missing-package" and "--simulate" in argv)
            or (case == "apt-lock" and package_apply)
        ):
            raise subprocess.CalledProcessError(100, argv, stderr=case)
        return subprocess.CompletedProcess(argv, 0, "", "")

    with pytest.raises((subprocess.SubprocessError, KeyboardInterrupt)):
        install.install_system(ROOT, run=run, paths=paths, release=RELEASE, architecture="amd64")
    journal = paths.journal.read_text()
    expected = (
        "metadata"
        if case in {"offline", "signature"}
        else ("resolution" if case == "missing-package" else "packages")
    )
    assert '"status": "failed"' in journal and f'"stage": "{expected}"' in journal
    assert '"complete"' not in journal
    assert not paths.backports.exists()
    assert all(
        "remove" not in call and "purge" not in call and "autoremove" not in call for call in calls
    )


def test_system_repeat_and_full_package_set(tmp_path: Path) -> None:
    paths = install.SystemPaths(
        tmp_path / "etc/setup-system.sources",
        tmp_path / "state/install.json",
        tmp_path / "keyring.gpg",
        tmp_path / "run",
        tmp_path / "policy-rc.d",
    )
    paths.keyring.write_bytes(b"test")
    paths.runtime.mkdir()
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "Inst hyprland (0.55.2 Debian)", "")

    install.install_system(ROOT, run=run, paths=paths, release=RELEASE, architecture="amd64")
    original = paths.backports.read_bytes()
    install.install_system(ROOT, run=run, paths=paths, release=RELEASE, architecture="amd64")
    assert paths.backports.read_bytes() == original and original.count(b"trixie-backports") == 1
    package_calls = [call for call in calls if "install" in call]
    assert all("--no-remove" in call for call in package_calls)
    assert any("hyprland=0.55.2+ds-1~bpo13+1" in call for call in package_calls)
    assert not paths.policy.exists()


def load_bootstrap():
    import importlib.util

    spec = importlib.util.spec_from_file_location("setup_bootstrap_test", ROOT / "bootstrap.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unknown_service_policy_stops_before_any_mutation(tmp_path: Path) -> None:
    paths = install.SystemPaths(
        tmp_path / "sources",
        tmp_path / "journal",
        tmp_path / "keyring",
        tmp_path / "run",
        tmp_path / "policy",
    )
    paths.keyring.write_bytes(b"trust")
    paths.policy.write_bytes(b"#!/bin/sh\nexit 0\n")
    with pytest.raises(ValueError, match="service policy"):
        install.install_system(
            ROOT,
            paths=paths,
            run=lambda *a, **kw: pytest.fail("APT ran"),
            release=RELEASE,
            architecture="amd64",
        )
    assert not paths.journal.exists() and paths.policy.read_bytes().endswith(b"exit 0\n")


def test_interrupted_owned_service_policy_is_removed_on_retry(tmp_path: Path) -> None:
    path = tmp_path / "policy"
    path.write_bytes(install.DEFER_POLICY)
    with install._defer_services(path):
        assert path.read_bytes() == install.DEFER_POLICY
    assert not path.exists()


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
    import sys
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
        install.run_command([sys.executable, "-c", code], timeout=1)
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


@pytest.mark.parametrize("user_code", [0, 5])
def test_orchestration_drops_uid_gid_groups_and_reports_real_user_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    user_code: int,
) -> None:
    boot = load_bootstrap()
    home = tmp_path / "home with spaces"
    home.mkdir()
    target = install.TargetUser("alice", os.getuid(), os.getgid(), home, "/bin/bash")
    monkeypatch.setattr(install, "current_packages", lambda root: ["hyprland=0.55.2+ds-1~bpo13+1"])
    monkeypatch.setattr(install, "resolve_target", lambda name: target)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "getgrouplist", lambda name, gid: [gid, 44])
    events: list[object] = []
    monkeypatch.setattr(install, "install_system", lambda *a, **kw: events.append("system"))
    monkeypatch.setattr(install, "mark_user_stage", lambda paths, status: events.append(status))

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        events.append("user")
        assert kwargs["user"] == target.uid and kwargs["group"] == target.gid
        assert kwargs["extra_groups"] == [target.gid, 44]
        env = kwargs["env"]
        assert isinstance(env, dict)
        assert env["HOME"] == str(home) and "GH_TOKEN" not in env
        assert env["XDG_CONFIG_HOME"] == str(home / "custom config")
        assert kwargs["cwd"] == home and kwargs["umask"] == 0o077
        return subprocess.CompletedProcess(argv, user_code, "user-output\n", "")

    monkeypatch.setattr(install, "run_command", run)
    assert (
        boot.main(
            ["apply", "--yes", "--user", "alice", "--xdg-config-home", str(home / "custom config")]
        )
        == user_code
    )
    assert events == ["system", "user", "complete" if user_code == 0 else "failed"]
    output = capsys.readouterr()
    assert ("Installation completed" in output.out) is (user_code == 0)
    assert not list(home.iterdir())


def test_plan_never_calls_runtime_or_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    boot = load_bootstrap()
    target = install.TargetUser("alice", os.getuid(), os.getgid(), tmp_path, "/bin/bash")
    monkeypatch.setattr(install, "current_packages", lambda root: ["hyprland=pin"])
    monkeypatch.setattr(install, "resolve_target", lambda name: target)
    monkeypatch.setattr(install, "install_system", lambda *a, **kw: pytest.fail("APT side effect"))
    monkeypatch.setattr(install, "run_command", lambda *a, **kw: pytest.fail("Runtime side effect"))
    monkeypatch.setattr(boot, "_managed_uv", lambda *a: pytest.fail("Download side effect"))
    assert boot.main(["plan"]) == 0
    assert not list(tmp_path.iterdir())


def test_user_runtime_uses_lock_without_checkout_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from setup_system import planning

    boot = load_bootstrap()
    target = install.TargetUser("alice", os.getuid(), os.getgid(), tmp_path, "/bin/bash")
    monkeypatch.setattr(install, "resolve_target", lambda name: target)
    monkeypatch.setattr(
        planning,
        "detect_platform",
        lambda: planning.Platform("debian", "13", "x86_64", True, False, ""),
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    for key, value in install.user_environment(target, {}).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(boot, "_managed_uv", lambda *a: Path("/fake/uv"))
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(install, "run_command", run)
    assert boot._user_runtime() == 0
    assert calls[0] == ["/fake/uv", "python", "install", "3.14.7"]
    assert "--locked" in calls[1] and "--no-editable" in calls[1]
    assert os.environ["UV_PROJECT_ENVIRONMENT"] == str(
        tmp_path / ".local/share/setup-system/runtime"
    )
    assert not (ROOT / ".venv/root-marker").exists()
    assert (tmp_path / ".local/share/setup-system/runtime.json").is_file()
    assert boot._user_runtime() == 0  # Existing owned receipt resumes the same runtime.


def test_unmanaged_runtime_is_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from setup_system import planning

    boot = load_bootstrap()
    target = install.TargetUser("alice", os.getuid(), os.getgid(), tmp_path, "/bin/bash")
    monkeypatch.setattr(install, "resolve_target", lambda name: target)
    monkeypatch.setattr(
        planning,
        "detect_platform",
        lambda: planning.Platform("debian", "13", "x86_64", True, False, ""),
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    for key, value in install.user_environment(target, {}).items():
        monkeypatch.setenv(key, value)
    runtime = tmp_path / ".local/share/setup-system/runtime"
    runtime.mkdir(parents=True)
    (runtime / "personal.txt").write_text("preserve")
    with pytest.raises(ValueError, match="unmanaged"):
        boot._user_runtime()
    assert (runtime / "personal.txt").read_text() == "preserve"


def test_timeout_propagates_through_nested_installer_supervisors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import signal
    import sys

    pid_file = tmp_path / "nested.pid"
    child_code = (
        "import os,signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "Path(" + repr(str(pid_file)) + ").write_text(str(os.getpid())); time.sleep(60)"
    )
    supervisor = (
        "import sys; from setup_system.installation import run_command; "
        'run_command([sys.executable,"-c",' + repr(child_code) + "],timeout=60)"
    )
    _wait_for_leaf_ready(monkeypatch, pid_file)
    with pytest.raises(subprocess.TimeoutExpired):
        install.run_command([sys.executable, "-c", supervisor], timeout=1, termination_grace=15)
    child = int(pid_file.read_text())
    try:
        status = Path(f"/proc/{child}/stat")
        assert not status.exists() or status.read_text().split(") ", 1)[1].startswith("Z ")
    finally:
        try:
            os.kill(child, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.parametrize("suffix", ["python", "bin", "runtime", "uv-cache"])
def test_uv_destinations_reject_symlinks_before_runtime_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    from setup_system import planning

    boot = load_bootstrap()
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = install.TargetUser("alice", os.getuid(), os.getgid(), home, "/bin/bash")
    monkeypatch.setattr(install, "resolve_target", lambda name: target)
    monkeypatch.setattr(
        planning,
        "detect_platform",
        lambda: planning.Platform("debian", "13", "x86_64", True, False, ""),
    )
    monkeypatch.setenv("HOME", str(home))
    for key, value in install.user_environment(target, {}).items():
        monkeypatch.setenv(key, value)
    path = (
        home / ".cache/setup-system/uv"
        if suffix == "uv-cache"
        else home / ".local/share/setup-system" / suffix
    )
    path.parent.mkdir(parents=True)
    path.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        boot._user_runtime()
    assert not list(outside.iterdir())
    assert not (home / ".local/share/setup-system/runtime.json").exists()


def test_unmanaged_uv_is_never_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    boot = load_bootstrap()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    target = tmp_path / "data/setup-system/bin/uv"
    target.parent.mkdir(parents=True)
    target.write_text("personal executable")
    monkeypatch.setattr(boot.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        boot.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "uv 0.1.0", ""),
    )
    monkeypatch.setattr(
        boot, "_read_limited", lambda *a: pytest.fail("Downloaded before conflict check")
    )
    with pytest.raises(RuntimeError, match="unmanaged"):
        boot._managed_uv(("debian", "x86_64"))
    assert target.read_text() == "personal executable"


def test_uv_staging_does_not_follow_predictable_stale_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib
    import io
    import tarfile

    boot = load_bootstrap()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    destination = tmp_path / "data/setup-system/bin"
    destination.mkdir(parents=True)
    personal = tmp_path / "personal"
    personal.write_bytes(b"preserve")
    (destination / f".uv-{os.getpid()}").symlink_to(personal)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        info = tarfile.TarInfo("uv-x86_64-unknown-linux-gnu/uv")
        info.size = 2
        bundle.addfile(info, io.BytesIO(b"uv"))
    archive = buffer.getvalue()
    monkeypatch.setitem(
        boot.UV_SHA256, "x86_64-unknown-linux-gnu", hashlib.sha256(archive).hexdigest()
    )
    monkeypatch.setattr(boot.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        boot.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "uv 0.12.15", ""),
    )
    monkeypatch.setattr(boot, "_read_limited", lambda *a: archive)
    assert boot._managed_uv(("debian", "x86_64")) == destination / "uv"
    assert personal.read_bytes() == b"preserve"
    assert (destination / "uv").read_bytes() == b"uv"


@pytest.mark.parametrize("case", ["root", "failure", "success"])
def test_user_config_stage_refuses_root_and_propagates_apply_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    from setup_system import user_install
    from setup_system.application import workstation

    monkeypatch.setattr(os, "geteuid", lambda: 0 if case == "root" else 1000)

    def apply(root: Path, profile: str) -> Path:
        if case == "root":
            pytest.fail("User apply reached with root identity")
        assert root == ROOT and profile == "hyprland"
        if case == "failure":
            raise ValueError("Unmanaged configuration requires review")
        return tmp_path / "backup-id"

    monkeypatch.setattr(workstation, "apply", apply)
    assert user_install.main(ROOT) == {"root": 2, "failure": 5, "success": 0}[case]
    output = capsys.readouterr()
    assert ("User configuration complete" in output.out) is (case == "success")
    assert not list(tmp_path.iterdir())


def test_nonexecutable_administrator_policy_stops_before_mutation(tmp_path: Path) -> None:
    paths = install.SystemPaths(
        tmp_path / "sources",
        tmp_path / "journal",
        tmp_path / "keyring",
        tmp_path / "run",
        tmp_path / "policy",
    )
    paths.keyring.write_bytes(b"trust")
    paths.policy.write_bytes(b"#!/bin/sh\nexit 101\n")
    paths.policy.chmod(0o644)
    with pytest.raises(ValueError, match="executable"):
        install.install_system(
            ROOT,
            paths=paths,
            run=lambda *a, **kw: pytest.fail("APT ran before policy validation"),
            release=RELEASE,
            architecture="amd64",
        )
    assert not paths.journal.exists()
    assert paths.policy.stat().st_mode & 0o777 == 0o644


def test_new_service_deferral_is_executable_with_restrictive_umask(tmp_path: Path) -> None:
    path = tmp_path / "policy"
    previous = os.umask(0o777)
    try:
        with install._defer_services(path):
            assert path.stat().st_mode & 0o100
    finally:
        os.umask(previous)
    assert not path.exists()


def test_owned_interrupted_policy_repairs_executable_mode_before_use(tmp_path: Path) -> None:
    path = tmp_path / "policy"
    path.write_bytes(install.DEFER_POLICY)
    path.chmod(0o644)
    with install._defer_services(path):
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
        install._write_system(path, b"replacement")
    assert path.read_bytes() == b"original"
