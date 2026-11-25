"""Run bounded subprocess groups and propagate caller cancellation."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import threading
import time
from collections.abc import Mapping
from contextlib import ExitStack, suppress
from io import BytesIO, TextIOWrapper
from tempfile import TemporaryFile
from types import FrameType
from typing import Literal, TypedDict, Unpack, overload

MAX_OUTPUT = 1024 * 1024


def _output(process: subprocess.Popen[bytes], timeout: int) -> tuple[bytes, bytes] | None:
    """Drain both pipes to EOF while retaining at most the allowed bytes per stream."""
    if process.stdout is None:
        process.wait(timeout=timeout)
        return b"", b""
    assert process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    oversized = False
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as reader:
        reader.register(process.stdout, selectors.EVENT_READ, stdout)
        reader.register(process.stderr, selectors.EVENT_READ, stderr)
        while reader.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout)
            for key, _ in reader.select(remaining):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    reader.unregister(key.fd)
                elif len(key.data) + len(chunk) <= MAX_OUTPUT and not oversized:
                    key.data.extend(chunk)
                else:
                    oversized = True
    process.wait(timeout=max(0, deadline - time.monotonic()))
    if oversized:
        return None
    return bytes(stdout), bytes(stderr)


def _text(data: bytes) -> str:
    """Match subprocess text mode, including locale and universal newlines."""
    with TextIOWrapper(BytesIO(data)) as stream:
        return stream.read()


class _RunOptions(TypedDict, total=False):
    timeout: int
    termination_grace: int
    check: bool
    input: str | None
    env: Mapping[str, str] | None
    stdin: int | None
    discard_output: bool


@overload
def run_command(
    argv: list[str], *, text: Literal[False], **options: Unpack[_RunOptions]
) -> subprocess.CompletedProcess[bytes]: ...


@overload
def run_command(
    argv: list[str], *, text: Literal[True] = True, **options: Unpack[_RunOptions]
) -> subprocess.CompletedProcess[str]: ...


def run_command(
    argv: list[str],
    *,
    timeout: int = 180,
    termination_grace: int = 5,
    check: bool = True,
    input: str | None = None,
    env: Mapping[str, str] | None = None,
    stdin: int | None = None,
    text: bool = True,
    discard_output: bool = False,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    """Run a native helper, retaining bounded output and reaping its process group."""
    if input is not None and stdin is not None:
        raise ValueError("Installer input cannot be combined with a separate stdin")

    def interrupted(signum: int, frame: FrameType | None) -> None:
        raise KeyboardInterrupt("Installer command interrupted")

    with ExitStack() as resources:
        if input is not None:
            stream = resources.enter_context(TemporaryFile(mode="w+"))
            stream.write(input)
            stream.seek(0)
            stdin = stream.fileno()
        if threading.current_thread() is threading.main_thread():
            previous = signal.signal(signal.SIGTERM, interrupted)
            resources.callback(signal.signal, signal.SIGTERM, previous)
        process = resources.enter_context(
            subprocess.Popen(
                argv,
                start_new_session=True,
                stdout=subprocess.DEVNULL if discard_output else subprocess.PIPE,
                stderr=subprocess.DEVNULL if discard_output else subprocess.PIPE,
                env=env,
                stdin=stdin,
            )
        )
        try:
            output = _output(process, timeout)
        except BaseException:
            if process.returncode is None:
                if threading.current_thread() is threading.main_thread():
                    signal.signal(signal.SIGTERM, signal.SIG_IGN)
                # Keep the leader unreaped throughout the grace period. Its PID
                # reserves the group identifier even if descendants outlive it.
                try:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGTERM)
                        time.sleep(termination_grace)
                finally:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
            raise
        if output is None:
            raise subprocess.SubprocessError("Command output exceeds the supported size")
        stdout, stderr = output
        result: subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]
        if text:
            result = subprocess.CompletedProcess(
                argv, process.returncode, _text(stdout), _text(stderr)
            )
        else:
            result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result
