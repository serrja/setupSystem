"""Run bounded subprocess groups and propagate caller cancellation."""

from __future__ import annotations

import os
import signal
import subprocess
from typing import Any


def run_command(
    argv: list[str],
    *,
    timeout: int = 180,
    termination_grace: int = 5,
    check: bool = True,
    capture_output: bool = True,
    text: bool = True,
    input: str | None = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Bound each process group and propagate cancellation through nested supervisors."""
    if not capture_output or not text:
        raise ValueError("Installer command capture must be textual")
    if input is not None:
        if "stdin" in kwargs:
            raise ValueError("Installer input cannot be combined with a separate stdin")
        kwargs["stdin"] = subprocess.PIPE

    def interrupted(signum: int, frame: Any) -> None:
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
            **kwargs,
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
