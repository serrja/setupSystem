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
from setup_system.application.installer import InstallRequest,Operation
from setup_system.tui.installer import InstallationApp
fcntl.ioctl(0,termios.TIOCSCTTY,0)
class Service:
 def run(self,request): raise AssertionError('Opening a terminal must not run an executor')
app=InstallationApp(Path.cwd(),InstallRequest(Operation.PLAN),service=Service())
app.run()
"""


def test_real_terminal_open_and_keyboard_quit() -> None:
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
