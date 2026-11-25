"""Run bounded subprocess groups and propagate caller cancellation."""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Mapping
from types import FrameType


def run_command(
    argv: list[str],
    *,
    timeout: int = 180,
    termination_grace: int = 5,
    check: bool = True,
    input: str | None = None,
    env: Mapping[str, str] | None = None,
    stdin: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """Bound each process group and propagate cancellation through nested supervisors."""
    if input is not None:
        if stdin is not None:
            raise ValueError("Installer input cannot be combined with a separate stdin")
        stdin = subprocess.PIPE

    def interrupted(signum: int, frame: FrameType | None) -> None:
        raise KeyboardInterrupt("Installer command interrupted")

    previous = signal.signal(signal.SIGTERM, interrupted)
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            argv,
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            stdin=stdin,
        )
        try:
            stdout, stderr = process.communicate(input=input, timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            # A parent may exit and close its pipes while a TERM-ignoring child lives.
            # Always escalate the remaining group, even when communicate returns early.
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=termination_grace)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            raise
        result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
        if check and result.returncode:
            raise subprocess.CalledProcessError(result.returncode, argv, stdout, stderr)
        return result
    finally:
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        signal.signal(signal.SIGTERM, previous)
