"""Read-only source probes must use the desktop account before package mutations."""

from __future__ import annotations

import json
import os
import pwd
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from setup_system import installation


@pytest.fixture
def source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, list[tuple[Path, bool]], installation.TargetUser]:
    assert os.geteuid() != 0, "Real source permission fixtures require an unprivileged test process"
    root = tmp_path / "source with spaces"
    (root / "src").mkdir(parents=True)
    (root / "src/example.py").write_text("raise RuntimeError('Source must never execute')\n")
    home = tmp_path / "desktop home"
    home.mkdir()
    record = pwd.struct_passwd(
        ("source_fixture", "x", os.getuid(), os.getgid(), "", str(home), "/bin/bash")
    )
    groups = sorted({record.pw_gid, *os.getgroups()})

    def lookup(name: str) -> pwd.struct_passwd:
        if name != record.pw_name:
            raise KeyError(name)
        return record

    def group_lookup(name: str, gid: int) -> list[int]:
        if name != record.pw_name or gid != record.pw_gid:
            raise KeyError(name)
        return groups

    monkeypatch.setattr(installation.pwd, "getpwnam", lookup)
    monkeypatch.setattr(installation.os, "getgrouplist", group_lookup)
    target = installation.TargetUser(
        record.pw_name, record.pw_uid, record.pw_gid, Path(record.pw_dir), record.pw_shell
    )
    return root, [(Path("src"), True), (Path("src/example.py"), False)], target


def snapshot(root: Path) -> dict[str, tuple[bytes, int, int, int]]:
    return {
        str(path.relative_to(root)): (
            path.read_bytes(),
            path.stat().st_mode,
            path.stat().st_uid,
            path.stat().st_gid,
        )
        for path in root.rglob("*")
        if path.is_file()
    }


def test_real_unprivileged_probe_reads_without_writes_or_source_execution(
    source: tuple[Path, list[tuple[Path, bool]], installation.TargetUser],
) -> None:
    root, paths, target = source
    before = snapshot(root)
    installation.validate_source_access(root, target, paths)
    assert snapshot(root) == before
    assert sorted(path.relative_to(root).as_posix() for path in root.rglob("*")) == [
        "src",
        "src/example.py",
    ]


def test_root_probe_drops_identity_and_groups_with_closed_environment(
    source: tuple[Path, list[tuple[Path, bool]], installation.TargetUser],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, paths, target = source
    groups = os.getgrouplist(target.name, target.gid)
    monkeypatch.setattr(installation.os, "geteuid", lambda: 0)
    for name in ("GH_TOKEN", "APT_CONFIG", "PYTHONPATH", "LD_PRELOAD", "DBUS_SESSION_BUS_ADDRESS"):
        monkeypatch.setenv(name, "fixture-must-not-forward")
    calls: list[list[str]] = []

    def probe(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert argv[:4] == [sys.executable, "-I", "-B", "-c"]
        assert len(argv) == 5
        assert options["user"] == target.uid
        assert options["group"] == target.gid
        assert options["extra_groups"] == groups
        assert options["umask"] == 0o077
        assert options["cwd"] == Path("/")
        environment = options["env"]
        assert isinstance(environment, dict)
        assert environment["HOME"] == str(target.home)
        assert environment["USER"] == environment["LOGNAME"] == target.name
        assert not set(environment) & {
            "GH_TOKEN",
            "APT_CONFIG",
            "PYTHONPATH",
            "LD_PRELOAD",
            "DBUS_SESSION_BUS_ADDRESS",
        }
        assert isinstance(options["input"], str)
        payload = json.loads(options["input"])
        assert payload == {"root": str(root), "paths": [[str(path), kind] for path, kind in paths]}
        return subprocess.CompletedProcess(argv, 0, '{"ok": true}\n', "")

    installation.validate_source_access(root, target, paths, run=probe)
    assert len(calls) == 1


def test_current_user_probe_does_not_attempt_a_privilege_transition(
    source: tuple[Path, list[tuple[Path, bool]], installation.TargetUser],
) -> None:
    root, paths, target = source
    calls: list[list[str]] = []

    def probe(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert not {"user", "group", "extra_groups"} & options.keys()
        return subprocess.CompletedProcess(argv, 0, '{"ok": true}\n', "")

    installation.validate_source_access(root, target, paths, run=probe)
    assert len(calls) == 1


@pytest.mark.parametrize(("relative", "mode"), [("src/example.py", 0), ("src", 0), ("src", 0o100)])
def test_actual_unreadable_source_is_rejected_without_mutation(
    source: tuple[Path, list[tuple[Path, bool]], installation.TargetUser], relative: str, mode: int
) -> None:
    root, paths, target = source
    before = snapshot(root)
    blocked = root / relative
    original_mode = blocked.stat().st_mode
    blocked.chmod(mode)
    try:
        with pytest.raises(ValueError, match=r"accessible|readable|source"):
            installation.validate_source_access(root, target, paths)
        assert blocked.stat().st_mode & 0o777 == mode
    finally:
        blocked.chmod(original_mode)
    assert snapshot(root) == before


@pytest.mark.parametrize("kind", ["root", "symlink", "special", "wrong-type"])
def test_source_root_and_entry_types_are_checked_by_the_real_user_probe(
    source: tuple[Path, list[tuple[Path, bool]], installation.TargetUser], kind: str
) -> None:
    root, paths, target = source
    original_mode = root.stat().st_mode
    if kind == "root":
        root.chmod(0)
    elif kind == "symlink":
        (root / "link").symlink_to(root / "src/example.py")
        paths.append((Path("link"), False))
    elif kind == "special":
        os.mkfifo(root / "pipe")
        paths.append((Path("pipe"), False))
    else:
        paths = [(Path("src/example.py"), True)]
    try:
        with pytest.raises(ValueError, match=r"accessible|source|symlink|regular|directory"):
            installation.validate_source_access(root, target, paths)
    finally:
        root.chmod(original_mode)
    assert (root / "src/example.py").read_text().startswith("raise RuntimeError")


@pytest.mark.parametrize(
    "kind", ["root", "other-user", "absolute", "traversal", "duplicate", "limit", "payload"]
)
def test_invalid_probe_inputs_stop_before_a_process_is_started(
    source: tuple[Path, list[tuple[Path, bool]], installation.TargetUser], kind: str
) -> None:
    root, paths, target = source
    if kind == "root":
        target = replace(target, uid=0)
    elif kind == "other-user":
        target = replace(target, uid=target.uid + 1)
    elif kind == "absolute":
        paths = [(root / "src/example.py", False)]
    elif kind == "traversal":
        paths = [(Path("../outside"), False)]
    elif kind == "duplicate":
        paths += paths
    elif kind == "limit":
        paths = [(Path(f"source-{index}"), False) for index in range(4097)]
    else:
        paths = [(Path("x" * 4000 + str(index)), False) for index in range(300)]

    def forbidden(*args: object, **options: object) -> subprocess.CompletedProcess[str]:
        pytest.fail("Invalid source probe inputs reached a child process")

    with pytest.raises(ValueError):
        installation.validate_source_access(root, target, paths, run=forbidden)


@pytest.mark.parametrize("kind", ["missing", "denied", "bad-response"])
def test_system_interpreter_failure_is_actionable_and_does_not_write_files(
    source: tuple[Path, list[tuple[Path, bool]], installation.TargetUser], kind: str
) -> None:
    root, paths, target = source
    before = snapshot(root)

    def unavailable(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        assert argv[0] == sys.executable
        if kind == "missing":
            raise FileNotFoundError("fixture missing system Python")
        if kind == "denied":
            raise PermissionError("fixture inaccessible system Python")
        return subprocess.CompletedProcess(argv, 0, "not a source probe acknowledgement", "")

    with pytest.raises(ValueError, match=r"Python|probe|interpreter"):
        installation.validate_source_access(root, target, paths, run=unavailable)
    assert snapshot(root) == before


def test_command_runner_delivers_text_input_without_executing_it() -> None:
    content = "fixture input with quotes ' and shell characters $(false)\n"
    result = installation.run_command(
        [sys.executable, "-I", "-B", "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
        input=content,
        timeout=10,
    )
    assert result.returncode == 0 and result.stdout == content


def test_command_runner_refuses_competing_stdin_before_starting_a_process() -> None:
    with pytest.raises(ValueError, match="separate stdin"):
        installation.run_command(["/does/not/exist"], input="fixture", stdin=subprocess.DEVNULL)


def test_source_probe_reuses_managed_interpreter_without_system_python(
    source: tuple[Path, list[tuple[Path, bool]], installation.TargetUser],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, paths, target = source
    interpreter = "/fixture/user runtime/bin/python"
    monkeypatch.setattr(installation.sys, "executable", interpreter)

    def execute(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        assert argv[:4] == [interpreter, "-I", "-B", "-c"]
        assert "/usr/bin/python3" not in argv[-1]
        return subprocess.CompletedProcess(argv, 0, '{"ok":true}', "")

    installation.validate_source_access(root, target, paths, run=execute)
