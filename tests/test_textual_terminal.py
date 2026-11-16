"""Exercise keyboard input through a real terminal driver without system operations."""

from __future__ import annotations

import errno
import fcntl
import json
import os
import pty
import selectors
import struct
import subprocess
import sys
import termios
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

CHILD = r"""
import fcntl
import json
import os
import sys
import termios
from pathlib import Path

from setup_system.application.installer import InstallRequest, Operation
from setup_system.tui.installer import InstallationApp, Prompt

fcntl.ioctl(0, termios.TIOCSCTTY, 0)
status_fd = int(sys.argv[1])
subscription = "https://fixture.invalid/terminal-subscription"

def report(record):
    os.write(status_fd, (json.dumps(record) + "\n").encode())

class Service:
    def __init__(self):
        self.requests = []
        self.interaction = None
        self.applied = False

    def run(self, request):
        self.requests.append(request)
        self.interaction.write("Complete fixture plan; no system operations")
        if request.operation == Operation.PLAN:
            return 0
        if not self.interaction.confirm("Apply complete fixture plan?"):
            return 2
        assert self.interaction.subscription() == subscription
        self.applied = True
        return 0

class TerminalApp(InstallationApp):
    def on_ready(self):
        self.previous = None
        self.set_interval(0.02, self.report_state)

    def report_state(self):
        modal = self.screen if isinstance(self.screen, Prompt) else None
        state = {
            "kind": "state",
            "phase": ("subscription" if modal.secret else "confirmation") if modal else "main",
            "focus": self.focused.id if self.focused else None,
            "operations": [r.operation.value for r in service.requests],
            "users": [r.user for r in service.requests],
            "busy": self.busy,
            "applied": service.applied,
            "code": self.last_code,
        }
        if state != self.previous:
            report(state)
            self.previous = state

service = Service()
app = TerminalApp(Path(sys.argv[2]), InstallRequest(Operation.PLAN), service=service)
service.interaction = app.interaction
code = app.run()
report({"kind": "exit", "code": code})
os.close(status_fd)
sys.exit(code or 0)
"""


class Terminal:
    def __init__(self, master: int, status: int, process: subprocess.Popen[bytes]) -> None:
        self.master = master
        self.process = process
        self.selector = selectors.DefaultSelector()
        self.selector.register(master, selectors.EVENT_READ, "output")
        self.selector.register(status, selectors.EVENT_READ, "status")
        self.output = bytearray()
        self.pending = bytearray()
        self.records: list[dict[str, object]] = []

    def send(self, value: bytes) -> None:
        os.write(self.master, value)

    def wait_for(
        self, predicate: Callable[[dict[str, object]], bool], stage: str
    ) -> dict[str, object]:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for record in self.records:
                if predicate(record):
                    return record
            for key, _ in self.selector.select(min(0.2, max(0, deadline - time.monotonic()))):
                try:
                    value = os.read(key.fd, 65536)
                except OSError as exc:
                    if key.data != "output" or exc.errno != errno.EIO:
                        raise
                    value = b""
                if not value:
                    self.selector.unregister(key.fileobj)
                    continue
                if key.data == "output":
                    self.output.extend(value)
                    assert len(self.output) <= 1024 * 1024, "Excessive terminal output"
                else:
                    self.pending.extend(value)
                    assert len(self.pending) <= 65536, "Excessive status record"
                    while b"\n" in self.pending:
                        line, _, remainder = self.pending.partition(b"\n")
                        self.pending = bytearray(remainder)
                        self.records.append(json.loads(line))
                        assert len(self.records) <= 100, "Excessive terminal state transitions"
        raise AssertionError(
            f"Timed out at {stage}; child exit={self.process.poll()}; "
            f"states={self.records[-8:]}; terminal tail={bytes(self.output[-300:])!r}"
        )


@contextmanager
def terminal(tmp_path: Path, term: str) -> Iterator[Terminal]:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    environment = {
        "PATH": os.defpath,
        "TERM": term,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "XDG_STATE_HOME": str(home / "state"),
        "XDG_DATA_HOME": str(home / "data"),
    }
    master, slave = pty.openpty()
    status, writer = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    session: Terminal | None = None
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 25, 80, 0, 0))
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", CHILD, str(writer), str(tmp_path)],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=environment,
            cwd=tmp_path,
            pass_fds=(writer,),
            start_new_session=True,
        )
        os.close(slave)
        os.close(writer)
        slave = writer = -1
        session = Terminal(master, status, process)
        yield session
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if session is not None:
            session.selector.close()
        for descriptor in (master, slave, status, writer):
            if descriptor >= 0:
                os.close(descriptor)


@pytest.mark.parametrize("term", ["linux", "xterm-256color"])
def test_keyboard_only_installation_flow_in_real_terminal(tmp_path: Path, term: str) -> None:
    with terminal(tmp_path, term) as session:
        session.wait_for(
            lambda s: s.get("phase") == "main" and s.get("focus") == "user",
            "initial target-user focus",
        )
        session.send(b"alice\r")
        plan = session.wait_for(
            lambda s: (
                s.get("operations") == ["plan"]
                and s.get("focus") == "plan"
                and s.get("busy") is False
            ),
            "read-only plan submitted with carriage return",
        )
        assert plan["users"] == ["alice"] and plan["applied"] is False
        session.send(b"\t\r")
        session.wait_for(
            lambda s: s.get("phase") == "confirmation" and s.get("focus") == "confirm",
            "explicitly focused plan confirmation",
        )
        session.send(b"\r")
        session.wait_for(
            lambda s: s.get("phase") == "subscription" and s.get("focus") == "secret",
            "private subscription input",
        )
        session.send(b"https://fixture.invalid/terminal-subscription\r")
        applied = session.wait_for(
            lambda s: (
                s.get("phase") == "main"
                and s.get("operations") == ["plan", "apply"]
                and s.get("focus") == "plan"
                and s.get("busy") is False
            ),
            "completed fixture apply",
        )
        assert applied["users"] == ["alice", "alice"]
        assert applied["applied"] is True and applied["code"] == 0
        session.send(b"\x11")
        result = session.wait_for(lambda s: s.get("kind") == "exit", "Ctrl+Q exit")
        assert result["code"] == 0
        assert session.process.wait(timeout=5) == 0
        assert b"https://fixture.invalid/terminal-subscription" not in session.output
