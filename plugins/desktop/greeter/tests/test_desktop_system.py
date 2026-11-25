"""Graphical login and boot changes stay finite, recoverable and independent of a GUI."""

from __future__ import annotations

import json
import os
import runpy
import shlex
import stat
import subprocess
import sys
import tomllib
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from setup_plugin_greeter import login as desktop
from setup_plugin_greeter.login_files import DesktopPaths
from setup_plugin_greeter.login_plan import XDG_KEYS

from setup_system.domain.identity import TargetUser
from setup_system.domain.system_facts import (
    ClockSnapshot,
    EncryptionInfo,
    EncryptionSnapshot,
    EncryptionState,
    SystemSnapshot,
)

ROOT = Path(__file__).resolve().parents[4]


class Commands:
    def __init__(self, paths: DesktopPaths) -> None:
        self.paths = paths
        self.default = "multi-user.target"
        self.calls: list[list[str]] = []

    def __call__(
        self,
        argv: list[str],
        *,
        env: dict[str, str],
        discard_output: bool,
        discard_stderr: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        assert timeout == 120 and discard_stderr
        assert discard_output == ("get-default" not in argv)
        assert env == {
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
        }
        if "get-default" in argv:
            return subprocess.CompletedProcess(argv, 0, self.default + "\n", "")
        if "enable" in argv:
            self.paths.display_manager.symlink_to(self.paths.vendor_unit)
        if "disable" in argv:
            self.paths.display_manager.unlink(missing_ok=True)
        if "set-default" in argv:
            self.default = argv[-1]
        return subprocess.CompletedProcess(argv, 0, "", "")


def system_fixture(
    tmp_path: Path,
) -> tuple[desktop.DesktopSystem, TargetUser, dict[str, str], Commands, SystemSnapshot]:
    system = tmp_path / "system"
    system.mkdir(mode=0o755)
    values = {
        field.name: system / str(getattr(DesktopPaths(), field.name)).lstrip("/")
        for field in fields(DesktopPaths)
        if field.name != "boundary"
    }
    paths = DesktopPaths(boundary=system, **values)
    paths.vendor_unit.parent.mkdir(parents=True, exist_ok=True)
    paths.vendor_unit.write_text("original fixture\n")
    paths.vendor_unit.chmod(0o644)
    home = tmp_path / "home with spaces"
    home.mkdir(mode=0o700)
    account = TargetUser("alice", os.getuid(), os.getgid(), home, "/bin/bash")
    environment = {key: str(home / key.lower()) for key in XDG_KEYS}
    launcher = Path(environment["XDG_DATA_HOME"]) / "setup-system/bin/start-hyprland"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\nexit 0\n")
    launcher.chmod(0o755)
    for directory in (*system.rglob("*"), *home.rglob("*")):
        if directory.is_dir():
            directory.chmod(0o755)
    encrypted = EncryptionInfo(state=EncryptionState.ENCRYPTED, layers=("crypt",), detail="fixture")
    snapshot = SystemSnapshot(
        encryption=EncryptionSnapshot(root=encrypted, home=encrypted),
        clock=ClockSnapshot(
            timezone="UTC",
            ntp_enabled=True,
            synchronized=True,
            rtc_local=False,
            can_ntp=True,
            providers=("chrony",),
            providers_known=True,
        ),
    )
    commands = Commands(paths)
    return (
        desktop.DesktopSystem(ROOT, run=commands, paths=paths, owner_uid=os.getuid()),
        account,
        environment,
        commands,
        snapshot,
    )


def test_plan_is_read_only_and_all_preauthentication_resources_are_system_owned(
    tmp_path: Path,
) -> None:
    system, account, environment, commands, snapshot = system_fixture(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    plan = system.plan(account, environment, snapshot=snapshot)
    assert {p: p.read_bytes() for p in before} == before
    assert {p for p in tmp_path.rglob("*") if p.is_file()} == set(before)
    config = tomllib.loads(
        next(f.content.decode() for f in plan.files if f.path == system.paths.config)
    )
    assert config["default_session"]["user"] == "_greetd"
    assert config["terminal"]["vt"] == 7 and config["general"]["source_profile"] is False
    assert "initial_session" not in config
    assert "cage -s" in config["default_session"]["command"]
    assert str(account.home) not in config["default_session"]["command"]
    assert all("get-default" in argv for argv in commands.calls)
    assert plan.snapshot.encryption.root.state == EncryptionState.ENCRYPTED
    assert all(not f.path.is_relative_to(system.paths.boundary / "boot") for f in plan.files)


def test_apply_enables_next_boot_and_repeat_has_no_duplicate_backup_or_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system, account, environment, commands, snapshot = system_fixture(tmp_path)
    monkeypatch.setattr(desktop, "_require_root", lambda: None)
    plan = system.plan(account, environment, snapshot=snapshot)
    system.apply(plan)
    assert commands.default == "graphical.target"
    assert system.paths.display_manager.is_symlink()
    assert all(
        not {"start", "restart", "--now", "isolate", "reboot", "grub-install"}.intersection(argv)
        for argv in commands.calls
    )
    assert not (system.paths.config.parent / "config.toml").exists()
    receipt = json.loads(system.paths.receipt.read_text())
    assert receipt["schema"] == 1 and receipt["owner"] == "setupSystem"
    assert len(receipt["files"]) == 5
    before = {
        p: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in system.paths.backups.rglob("*")
        if p.is_file()
    }
    plan = system.plan(account, environment, snapshot=snapshot)
    commands.calls.clear()
    system.apply(plan)
    assert all("get-default" in argv for argv in commands.calls)
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before} == before


@pytest.mark.parametrize(
    "kind",
    [
        "different-manager",
        "unknown-config",
        "edited-managed",
        "symlink",
        "hardlink",
    ],
)
def test_unknown_or_unsafe_system_state_refuses_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    system, account, environment, commands, snapshot = system_fixture(tmp_path)
    if kind == "different-manager":
        system.paths.display_manager.parent.mkdir(parents=True, exist_ok=True)
        system.paths.display_manager.symlink_to("/usr/lib/systemd/system/sddm.service")
    else:
        path = system.paths.config
        path.parent.mkdir(parents=True)
        path.parent.chmod(0o755)
        if kind == "edited-managed":
            monkeypatch.setattr(desktop, "_require_root", lambda: None)
            system.apply(system.plan(account, environment, snapshot=snapshot))
        if kind == "symlink":
            path.symlink_to(system.paths.vendor_unit)
        elif kind == "hardlink":
            os.link(system.paths.vendor_unit, path)
        else:
            path.write_text("administrator configuration\n")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    commands.calls.clear()
    with pytest.raises(ValueError):
        system.plan(account, environment, snapshot=snapshot)
    assert {p: p.read_bytes() for p in before} == before
    assert not any("enable" in argv or "set-default" in argv for argv in commands.calls)


def test_nonroot_apply_fails_before_files_or_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system, account, environment, commands, snapshot = system_fixture(tmp_path)
    plan = system.plan(account, environment, snapshot=snapshot)
    commands.calls.clear()
    monkeypatch.setattr(desktop.os, "geteuid", lambda: 12345)
    with pytest.raises(PermissionError):
        system.apply(plan)
    assert not commands.calls and not system.paths.config.exists()


def test_postplan_conflict_and_missing_user_launcher_cannot_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system, account, environment, _commands, snapshot = system_fixture(tmp_path)
    plan = system.plan(account, environment, snapshot=snapshot)
    monkeypatch.setattr(desktop, "_require_root", lambda: None)
    system.paths.config.parent.mkdir(parents=True, exist_ok=True)
    system.paths.config.write_text("later admin change\n")
    with pytest.raises(ValueError, match="changed after planning"):
        system.apply(plan)
    assert system.paths.config.read_text() == "later admin change\n"


def load_bridge() -> dict[str, Any]:
    return runpy.run_path(
        str(Path(desktop.__file__).parent / "assets/login/login_session.py"),
        run_name="fixture_login_bridge",
    )


def test_postpam_bridge_uses_only_matching_uid_registry_and_closed_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system, account, environment, _, snapshot = system_fixture(tmp_path)
    plan = system.plan(account, environment, snapshot=snapshot)
    registry = tmp_path / "registry"
    registry.mkdir(mode=0o755)
    (registry / f"{account.uid}.json").write_bytes(
        next(f.content for f in plan.files if f.path.parent == system.paths.registry)
    )
    runtime = account.home / "runtime"
    runtime.mkdir(mode=0o700)
    (registry / f"{account.uid}.json").chmod(0o644)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("XDG_SESSION_ID", "fixture-session")
    monkeypatch.setenv("UNRELATED_SECRET", "fixture-only")
    namespace = load_bridge()
    monkeypatch.setattr(
        namespace["pwd"],
        "getpwuid",
        lambda uid: SimpleNamespace(
            pw_gid=account.gid, pw_name=account.name, pw_dir=str(account.home), pw_shell="/bin/bash"
        ),
    )
    arguments, child = namespace["session"](registry, owner=os.getuid(), boundary=registry)
    assert arguments == [
        "/bin/sh",
        str(Path(environment["XDG_DATA_HOME"]) / "setup-system/bin/start-hyprland"),
        "--display-manager",
    ]
    assert child["HOME"] == str(account.home) and "UNRELATED_SECRET" not in child
    assert child["XDG_SESSION_TYPE"] == "wayland" and "DISPLAY" not in child
    record = registry / f"{account.uid}.json"
    data = json.loads(record.read_text())
    data["uid"] = account.uid + 1
    record.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="authenticated account"):
        namespace["session"](registry, owner=os.getuid(), boundary=registry)


@pytest.mark.parametrize("state", [EncryptionState.LOCKED, EncryptionState.UNKNOWN])
@pytest.mark.parametrize("layer", ["fscrypt", "ecryptfs"])
def test_locked_home_refuses_before_system_commands(
    tmp_path: Path, state: EncryptionState, layer: str
) -> None:
    system, account, environment, commands, snapshot = system_fixture(tmp_path)
    locked = EncryptionInfo(state=state, layers=(layer,), detail="fixture locked HOME")
    snapshot = snapshot.validated(encryption=snapshot.encryption.validated(home=locked))
    with pytest.raises(
        ValueError, match="encrypted HOME is locked or its unlock status is unknown"
    ):
        system.plan(account, environment, snapshot=snapshot)
    assert not commands.calls and not system.paths.config.exists()


def test_login_failure_restores_graphical_target_and_preserves_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system, account, environment, commands, snapshot = system_fixture(tmp_path)
    snapshot = snapshot.validated(clock=snapshot.clock.validated(ntp_enabled=False))
    plan = system.plan(account, environment, snapshot=snapshot)
    original_write = system.files.move_new

    def move(source: Path, path: Path) -> None:
        if path == system.paths.receipt:
            raise OSError("fixture receipt failure")
        original_write(source, path)

    monkeypatch.setattr(system.files, "move_new", move)
    monkeypatch.setattr(desktop, "_require_root", lambda: None)
    with pytest.raises(OSError, match="fixture receipt failure"):
        system.apply(plan)
    changes = [argv for argv in commands.calls if "set-ntp" in argv]
    assert not changes
    assert commands.default == "multi-user.target"
    assert not system.paths.receipt.exists()
    assert not system.paths.display_manager.exists()
    assert all(not item.path.exists() for item in plan.files)


def test_postpam_bridge_rejects_primary_group_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    namespace = load_bridge()
    monkeypatch.setattr(
        namespace["pwd"], "getpwuid", lambda uid: SimpleNamespace(pw_gid=os.getegid())
    )
    monkeypatch.setattr(namespace["os"], "getgid", lambda: os.getegid() + 1)
    with pytest.raises(ValueError, match="primary group"):
        namespace["session"](tmp_path)


def test_login_bridge_interpreter_is_isolated_before_any_python_imports(tmp_path: Path) -> None:
    sentinel = tmp_path / "untrusted-python-import-ran"
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    (hostile / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('unsafe')\n"
    )
    source = (Path(desktop.__file__).parent / "assets/login/login_session.py").read_text()
    shebang, body = source.split("\n", 1)
    assert shebang == "#!/usr/bin/python3 -IB"
    bridge = tmp_path / "login-session"
    bridge.write_text(f"#!{sys.executable} -IB\n{body}")
    bridge.chmod(0o755)
    result = subprocess.run(
        [str(bridge)],
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(hostile), "HOME": str(hostile)},
        check=False,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 4 and "unavailable" in result.stderr
    assert not sentinel.exists()
    assert not list(tmp_path.rglob("__pycache__"))


@pytest.mark.parametrize(
    "field", [None, "delayed", "Active", "Seat", "VTNr", "Remote", "User", "Class"]
)
def test_display_manager_launcher_requires_an_active_local_logind_seat(
    tmp_path: Path, field: str | None
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    marker = tmp_path / "started"
    details = {
        "User": str(os.getuid()),
        "Remote": "no",
        "Class": "user",
        "Active": "yes",
        "Seat": "seat0",
        "VTNr": "7",
    }
    if field not in (None, "delayed"):
        details[field] = {
            "Active": "no",
            "Seat": "",
            "VTNr": "0",
            "Remote": "yes",
            "User": "0",
            "Class": "greeter",
        }[field]
    (fake_bin / "loginctl").write_text(
        "#!/bin/sh\nprintf '%s\\n' "
        + " ".join(shlex.quote(f"{key}={value}") for key, value in details.items())
        + "\n"
    )
    if field == "delayed":
        pending = tmp_path / "session-was-pending"
        (fake_bin / "loginctl").write_text(
            "#!/bin/sh\n"
            f"if [ ! -e {shlex.quote(str(pending))} ]; then\n"
            f": > {shlex.quote(str(pending))}\nprintf '%s\\n' 'Active=no'\n"
            "else\nprintf '%s\\n' 'Active=yes'\nfi\n"
            "printf '%s\\n' "
            + " ".join(
                shlex.quote(f"{key}={value}") for key, value in details.items() if key != "Active"
            )
            + "\n"
        )
    (fake_bin / "dbus-run-session").write_text(
        f"#!/bin/sh\nprintf '%s' started > {shlex.quote(str(marker))}\n"
    )
    for executable in fake_bin.iterdir():
        executable.chmod(0o755)
    source = (
        ROOT / "plugins/desktop/hyprland/src/setup_plugin_hyprland/assets/start-hyprland.sh"
    ).read_text()
    for name in ("CONFIG", "DATA", "STATE", "CACHE"):
        source = source.replace(f"@{name}_HOME@", shlex.quote(str(tmp_path / name.lower())))
    launcher = tmp_path / "start-hyprland"
    launcher.write_text(source)
    result = subprocess.run(
        ["/bin/sh", str(launcher), "--display-manager"],
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "XDG_SESSION_ID": "fixture-session",
            "XDG_RUNTIME_DIR": str(runtime),
        },
        check=False,
        text=True,
        capture_output=True,
        timeout=10,
    )
    ready = field in (None, "delayed")
    assert result.returncode == (0 if ready else 4), result.stderr
    assert marker.exists() == ready


def test_partial_recovery_failure_still_attempts_service_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system, account, environment, commands, snapshot = system_fixture(tmp_path)
    snapshot = snapshot.validated(clock=snapshot.clock.validated(ntp_enabled=False))
    plan = system.plan(account, environment, snapshot=snapshot)
    original_write, original_command = system.files.move_new, system._command

    def move(source: Path, path: Path) -> None:
        if path == system.paths.receipt:
            raise OSError("fixture receipt failure")
        original_write(source, path)

    def command(argv: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        if argv[-2:] == ["set-default", "multi-user.target"]:
            raise subprocess.CalledProcessError(1, argv)
        return original_command(argv, check=check)

    monkeypatch.setattr(system.files, "move_new", move)
    monkeypatch.setattr(system, "_command", command)
    monkeypatch.setattr(desktop, "_require_root", lambda: None)
    with pytest.raises(RuntimeError, match="recovery incomplete"):
        system.apply(plan)
    assert all(call[0] != "/usr/bin/timedatectl" for call in commands.calls)
    assert ["/usr/bin/systemctl", "--system", "disable", "greetd.service"] in commands.calls
    assert commands.calls[-1] == ["/usr/bin/systemctl", "--system", "daemon-reload"]


@pytest.mark.parametrize(
    "kind", ["valid", "stale", "tcp", "missing", "symlink", "wrong-owner", "regular"]
)
def test_login_bridge_retains_only_an_existing_owned_user_bus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    system, account, environment, _, snapshot = system_fixture(tmp_path)
    plan = system.plan(account, environment, snapshot=snapshot)
    registry = tmp_path / "registry"
    registry.mkdir(mode=0o755)
    record = registry / f"{account.uid}.json"
    record.write_bytes(
        next(f.content for f in plan.files if f.path.parent == system.paths.registry)
    )
    record.chmod(0o644)
    runtime = account.home / "runtime"
    runtime.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    address = f"unix:path={runtime / 'bus'}"
    if kind == "stale":
        address = "unix:path=/run/user/999/bus"
    elif kind == "tcp":
        address = "tcp:host=example.invalid,port=1"
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", address)
    namespace = load_bridge()
    monkeypatch.setattr(
        namespace["pwd"],
        "getpwuid",
        lambda uid: SimpleNamespace(
            pw_gid=account.gid, pw_name=account.name, pw_dir=str(account.home), pw_shell="/bin/bash"
        ),
    )
    original_lstat = Path.lstat

    def lstat(path: Path) -> os.stat_result:
        if path == runtime / "bus":
            if kind == "missing":
                raise FileNotFoundError("fixture user bus absent")
            mode = {"symlink": stat.S_IFLNK, "regular": stat.S_IFREG}.get(kind, stat.S_IFSOCK)
            return SimpleNamespace(
                st_mode=mode | 0o600, st_uid=account.uid + (kind == "wrong-owner")
            )
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    _, child = namespace["session"](registry, owner=os.getuid(), boundary=registry)
    assert ("DBUS_SESSION_BUS_ADDRESS" in child) == (kind == "valid")


@pytest.mark.parametrize("kind", ["relative", "parent", "public", "symlink"])
def test_login_bridge_rejects_unsafe_runtime_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    system, account, environment, _, snapshot = system_fixture(tmp_path)
    plan = system.plan(account, environment, snapshot=snapshot)
    registry = tmp_path / "registry"
    registry.mkdir(mode=0o755)
    record = registry / f"{account.uid}.json"
    record.write_bytes(
        next(f.content for f in plan.files if f.path.parent == system.paths.registry)
    )
    record.chmod(0o644)
    runtime = account.home / "runtime"
    runtime.mkdir(mode=0o700)
    selected = str(runtime)
    if kind == "relative":
        selected = "runtime"
    elif kind == "parent":
        selected = str(account.home / ".." / account.home.name / "runtime")
    elif kind == "public":
        runtime.chmod(0o755)
    else:
        link = account.home / "linked-runtime"
        link.symlink_to(runtime, target_is_directory=True)
        selected = str(link)
    monkeypatch.setenv("XDG_RUNTIME_DIR", selected)
    namespace = load_bridge()
    monkeypatch.setattr(
        namespace["pwd"],
        "getpwuid",
        lambda uid: SimpleNamespace(
            pw_gid=account.gid, pw_name=account.name, pw_dir=str(account.home), pw_shell="/bin/bash"
        ),
    )
    with pytest.raises(ValueError, match="private absolute runtime directory"):
        namespace["session"](registry, owner=os.getuid(), boundary=registry)


def test_login_plan_passes_registry_as_one_session_argument(tmp_path: Path):
    root = tmp_path / "login with 'quote'"
    root.mkdir()
    system, account, environment, _, snapshot = system_fixture(root)
    plan = system.plan(account, environment, snapshot=snapshot)
    bridge = next(item for item in plan.files if item.path == system.paths.bridge)
    config = next(item for item in plan.files if item.path == system.paths.config)
    assert (
        bridge.content
        == (Path(desktop.__file__).parent / "assets/login/login_session.py").read_bytes()
    )
    args = shlex.split(tomllib.loads(config.content.decode())["default_session"]["command"])
    assert shlex.split(args[args.index("-c") + 1]) == [
        str(system.paths.bridge),
        str(system.paths.registry),
    ]


@pytest.mark.parametrize(
    "args", [(), ("/tmp/user-supplied",), ("/etc/setup-system/login-users", "extra")]
)
def test_bridge_rejects_registry_redirection_before_session_lookup(
    args: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
):
    import sys

    bridge = load_bridge()
    main = bridge["main"]
    assert callable(main)
    monkeypatch.setattr(sys, "argv", ["login-session", *args])
    monkeypatch.setitem(
        main.__globals__,
        "session",
        lambda *args: pytest.fail("Unexpected registry reached session lookup"),
    )
    assert main() == 4


def test_bridge_passes_installed_registry_to_authenticated_session(monkeypatch: pytest.MonkeyPatch):
    import sys

    bridge = load_bridge()
    main = bridge["main"]
    assert callable(main)
    paths = []

    def session(path):
        paths.append(path)
        raise ValueError("Stop after checking the registry argument")

    monkeypatch.setattr(sys, "argv", ["login-session", str(DesktopPaths().registry)])
    monkeypatch.setitem(main.__globals__, "session", session)
    assert main() == 4 and paths == [DesktopPaths().registry]


@pytest.mark.parametrize("change", ["default-target", "enabled", "files"])
def test_native_consent_rejects_last_minute_changes(tmp_path, monkeypatch, change):
    system, account, env, commands, snapshot = system_fixture(tmp_path)
    plan = system.plan(account, env, snapshot=snapshot)
    monkeypatch.setattr(desktop, "_require_root", lambda: None)
    if change == "default-target":
        commands.default = "rescue.target"
    elif change == "enabled":
        system.paths.display_manager.parent.mkdir(parents=True, exist_ok=True)
        system.paths.display_manager.symlink_to(system.paths.vendor_unit)
    else:
        system.paths.config.parent.mkdir(parents=True, exist_ok=True)
        system.paths.config.write_text("local administrator edit")
    with pytest.raises(ValueError):
        system.revalidate(plan.consent, account, env, snapshot=snapshot)
    assert not system.paths.receipt.exists()
    assert not any("set-default" in call or "enable" in call for call in commands.calls)


def test_activation_handoff_preserves_session_and_checks_default_target(tmp_path, monkeypatch):
    import asyncio

    import setup_plugin_greeter as plugin
    from setup_plugin_greeter import login_boot, login_plan

    from setup_system_sdk import Inputs, Outcome, PluginSettings

    system, account, environment, commands, machine = system_fixture(tmp_path)
    roots = {
        key.removeprefix("XDG_").removesuffix("_HOME").lower(): Path(value)
        for key, value in environment.items()
    }
    monkeypatch.setattr(plugin, "DesktopSystem", lambda root: system)
    monkeypatch.setattr(plugin, "resolve_target", lambda name: account)
    monkeypatch.setattr(plugin, "current_target", lambda: ("amd64", True))
    monkeypatch.setattr(plugin, "current_boot_identity", lambda: "fixture-boot")
    monkeypatch.setattr(plugin, "desktop_directories", lambda: roots)
    monkeypatch.setattr(login_plan, "inspect_system", lambda home: machine)
    monkeypatch.setattr(desktop, "_require_root", lambda: None)

    def bridge(plugin_id, target, payload, method):
        assert (plugin_id, target, method) == ("greeter", "activate.yml", "sudo")
        change = plugin.Confirmation.model_validate(payload)
        assert change.user == account.name and change.roots == roots
        plugin.activate(change)

    monkeypatch.setattr(plugin, "run_native_play", bridge)
    backend, settings, inputs = plugin.Plugin(), PluginSettings(), Inputs()
    plan = asyncio.run(backend.plan("activate-login", settings, inputs))
    assert plan.outcome == Outcome.READY and plan.resources[-1].identity == "fixture-boot"
    assert asyncio.run(backend.verify(plan, settings, inputs)).outcome == Outcome.PARTIAL
    plugin.apply_native("activate-login", settings, inputs, plan)
    assert commands.default == "graphical.target"
    assert asyncio.run(backend.verify(plan, settings, inputs)).outcome == Outcome.CHANGED
    repeated = asyncio.run(backend.plan("activate-login", settings, inputs))
    assert repeated.outcome == Outcome.NO_CHANGES
    with pytest.raises(ValueError, match="changed"):
        plugin.apply_native("activate-login", settings, inputs, plan)
    assert not any(set(args) & {"start", "restart", "reboot", "isolate"} for args in commands.calls)
    monkeypatch.setattr(login_boot, "manager_enabled", lambda files: True)
    assert asyncio.run(backend.plan("uninstall", settings, inputs)).outcome == Outcome.BLOCKED
