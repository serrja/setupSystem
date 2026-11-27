"""Reap one Linux worker tree, including children which start a new session."""

import ctypes
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import FrameType

PR_SET_PDEATHSIG = 1
PR_SET_CHILD_SUBREAPER = 36


def supervise(command: list[str], grace: float, descriptor: int, host_pid: int) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    # Adopt worker orphans, including PTY clients, before waitpid can report ECHILD.
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Cannot supervise worker descendants")
    children = Path("/proc/thread-self/children")
    children.read_text()
    stopping = False

    def stop(_signum: int, _frame: FrameType | None) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    if host_pid:
        if libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "Cannot monitor worker parent")
        if os.getppid() != host_pid:
            return 1

    worker = subprocess.Popen(command, pass_fds=(descriptor,) if descriptor >= 0 else ())
    if descriptor >= 0:
        os.close(descriptor)
    os.close(0)
    os.close(1)
    watched = {signal.SIGCHLD, signal.SIGTERM}
    signal.pthread_sigmask(signal.SIG_BLOCK, watched)
    deadline = None
    notified: set[int] = set()
    while True:
        try:
            pid, code = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return 1 if notified or worker.returncode is None else worker.returncode
        if pid:
            if pid == worker.pid:
                worker.returncode = os.waitstatus_to_exitcode(code)
                stopping = True
            continue
        if not stopping:
            stopping = signal.sigwait(watched) == signal.SIGTERM
            continue
        now = time.monotonic()
        if deadline is None:
            deadline = now + grace
        # We signal only our own unreaped children. Once a parent exits, the next
        # generation is adopted even when it used setsid or double-forked.
        for child in map(int, children.read_text().split()):
            if child not in notified or now >= deadline:
                try:
                    os.kill(child, signal.SIGKILL if now >= deadline else signal.SIGTERM)
                except ProcessLookupError:
                    pass
                notified.add(child)
        signal.sigtimedwait(watched, 0.025)


if __name__ == "__main__":
    sys.exit(supervise(sys.argv[4:], float(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])))
