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
from pathlib import Path
from setup_system.composition import build_app
fcntl.ioctl(0,termios.TIOCSCTTY,0)
app=build_app(Path.cwd())
app.run()
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
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    output.extend(os.read(master, 65536))
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
            if b"setupSystem" in output:
                break
        assert b"setupSystem" in output
        os.write(master, b"\x11")
        assert process.wait(timeout=15) == 0
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        os.close(master)
