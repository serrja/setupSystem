"""Exercise native terminal ownership using a disposable local PTY and synthetic client."""

import asyncio
import os
import socket
import sys
from pathlib import Path

import pytest
from test_plugin_lifecycle import activation

from setup_system import plugin_manager
from setup_system.configuration import Settings
from setup_system.plugin_manager import AsyncMakeRunner, Snapshot
from setup_system_sdk import Mutation, Outcome, Plan, Result, user_terminal


@pytest.fixture
def display(home, monkeypatch):
    runtime = home / "session"
    runtime.mkdir(mode=0o700)
    monkeypatch.chdir(runtime)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("WAYLAND_DISPLAY", "display")
    monkeypatch.setattr(user_terminal, "_FOOT", Path(sys.executable))
    monkeypatch.setattr(user_terminal, "protected", lambda path, *, directory: None)
    with socket.socket(socket.AF_UNIX) as local:
        local.bind("display")
        yield runtime


def test_native_client_has_its_own_terminal_and_only_the_required_environment(
    home, display, monkeypatch, capfd
):
    terminal = home / "terminal"
    terminal.write_text(
        "#!" + sys.executable + "\n"
        "import errno, os, pty, sys\n"
        "assert sys.argv[1:3] == ['--config=/dev/null', '--']\n"
        "pid, fd = pty.fork()\n"
        "if pid == 0: os.execv(sys.argv[3], sys.argv[3:])\n"
        "try:\n"
        "    while os.read(fd, 64): pass\n"
        "except OSError as error:\n"
        "    if error.errno != errno.EIO: raise\n"
        "finally: os.close(fd)\n"
        "sys.exit(os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]))\n"
    )
    terminal.chmod(0o700)
    monkeypatch.setattr(user_terminal, "_FOOT", terminal)
    monkeypatch.setenv("SETUP_SYSTEM_NATIVE_TERMINAL", "1")
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-credential")
    monkeypatch.setenv("BASH_ENV", "/invalid-fixture")
    client = home / "fixture.py"
    client.write_text(
        "import os, pathlib, sys\n"
        "assert all(os.isatty(fd) for fd in range(3))\n"
        "hidden = {'GITHUB_TOKEN', 'BASH_ENV', 'SETUP_SYSTEM_NATIVE_TERMINAL'}\n"
        "assert not hidden & os.environ.keys()\n"
        "print('synthetic-private-prompt')\n"
        "print('synthetic-private-error', file=sys.stderr)\n"
        "pathlib.Path('readback').write_text(str(sys.argv[1:]))\n"
        "sys.exit(7)\n"
    )
    args = ["name with spaces", "Ключ", "--literal"]
    assert user_terminal.available()
    assert user_terminal.run_native([sys.executable, str(client), *args], cwd=home) == 7
    assert (home / "readback").read_text() == str(args)
    streams = capfd.readouterr()
    assert not streams.out and not streams.err


def test_window_requires_confirmed_action_local_socket_and_executable(display, monkeypatch):
    assert user_terminal.available()
    with pytest.raises(ValueError, match="confirmed"):
        user_terminal.run_native([sys.executable])
    monkeypatch.setenv("SETUP_SYSTEM_NATIVE_TERMINAL", "1")
    for args in ([], ["relative"], ["/missing-client"], [sys.executable, "bad\x00value"]):
        with pytest.raises(ValueError):
            user_terminal.run_native(args)
    with pytest.raises(ValueError, match="working directory"):
        user_terminal.run_native([sys.executable], cwd=Path("relative"))
    display.chmod(0o755)
    assert not user_terminal.available()
    display.chmod(0o700)
    (display / "alias").symlink_to(display / "display")
    for name in ("alias", "../display", str(display / "display"), "missing"):
        monkeypatch.setenv("WAYLAND_DISPLAY", name)
        assert not user_terminal.available()
    monkeypatch.setenv("WAYLAND_DISPLAY", "display")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert not user_terminal.available()


@pytest.mark.parametrize("interactive", [False, True])
def test_action_descriptor_selects_terminal_permission_and_deadline(home, monkeypatch, interactive):
    state = activation(home)
    if interactive:
        state.manifest.permissions.append(user_terminal.PERMISSION)
        state.manifest.actions[0].permissions.append(user_terminal.PERMISSION)
    options = Settings.application()
    manager = AsyncMakeRunner(options)
    snap = Snapshot(
        plugin_id=state.manifest.id,
        digest=state.digest,
        mutation=Mutation(
            action_id="configure", settings={}, inputs={}, plan=Plan(summary="Fixture")
        ),
    )

    async def pipe(argv, payload, env, cwd, timeout):
        assert ("SETUP_SYSTEM_NATIVE_TERMINAL" in env) == interactive
        assert timeout == (
            options.terminal_timeout_seconds if interactive else options.idle_timeout_seconds
        )
        return Result(outcome=Outcome.NO_CHANGES, message="Fixture").model_dump_json().encode()

    monkeypatch.setattr(plugin_manager, "_pipe", pipe)
    monkeypatch.setenv("SETUP_SYSTEM_NATIVE_TERMINAL", "untrusted")
    result = asyncio.run(manager.execute(state, snap, None))
    assert result.outcome == Outcome.NO_CHANGES
