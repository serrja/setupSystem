"""A real terminal can open and quit Textual without entering a system executor."""

import errno
import os
import pty
import select
import signal
import subprocess
import sys
import time

CHILD = """import fcntl,termios,sys
from setup_system.app import main
fcntl.ioctl(0,termios.TIOCSCTTY,0)
raise SystemExit(main())
"""


def test_real_terminal_open_and_keyboard_quit(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    for scope in ("config", "data", "state", "cache"):
        monkeypatch.setenv("XDG_" + scope.upper() + "_HOME", str(tmp_path / scope))
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, "-c", CHILD],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        start_new_session=True,
        env={**os.environ, "TERM": "xterm-256color"},
    )
    os.close(slave)
    output = bytearray()
    try:
        quit_sent = False
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if not select.select([master], [], [], 0.1)[0]:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            output.extend(chunk)
            if not quit_sent and b"setupSystem" in output:
                os.write(master, b"\x11")
                quit_sent = True
                deadline = time.monotonic() + 15
        else:
            message = "Textual timed out while quitting" if quit_sent else "Textual did not open"
            raise AssertionError(message)
        assert quit_sent
        assert process.wait(timeout=max(0, deadline - time.monotonic())) == 0
        assert b"Traceback (most recent call last)" not in output
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        os.close(master)


def test_invalid_application_override_exits_before_opening_forms(tmp_path):
    master, slave = pty.openpty()
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-m", "setup_system.app"],
            stdin=slave,
            stdout=slave,
            stderr=subprocess.PIPE,
            env={
                "HOME": str(tmp_path),
                "TERM": "xterm-256color",
                "SETUP_SYSTEM_SUGGESTION_RESULTS": "invalid-private-value",
            },
            timeout=10,
            check=False,
        )
        assert result.returncode == 1
        assert b"invalid-private-value" not in result.stderr and b"Traceback" not in result.stderr
        assert not list(tmp_path.iterdir())
    finally:
        os.close(slave)
        os.close(master)
