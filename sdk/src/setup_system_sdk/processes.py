"""Bounded process groups for workers, read-only probes and native tools."""

from __future__ import annotations

import math
import os
import signal
import subprocess
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO, TextIOWrapper
from pathlib import Path
from typing import Literal, TypedDict, Unpack, overload

import anyio
from anyio.abc import ByteReceiveStream, Process

MAX_OUTPUT = 1024 * 1024


class CancellationPolicy(StrEnum):
    TERMINATE = "terminate"
    SAFE_BOUNDARY = "safe_boundary"


@dataclass(frozen=True)
class ProcessSpec:
    argv: tuple[str, ...]
    cwd: Path | None = None
    env: Mapping[str, str] | None = None
    stdin: int | None = subprocess.DEVNULL
    pass_fds: tuple[int, ...] = ()
    timeout: float = 180
    stdout_limit: int = MAX_OUTPUT
    stderr_limit: int = MAX_OUTPUT
    discard_stdout: bool = False
    discard_stderr: bool = False
    grace: float = 1
    cancellation: CancellationPolicy = CancellationPolicy.TERMINATE
    safe_boundary: Callable[[Process], Awaitable[None]] | None = None

    def __post_init__(self) -> None:
        if not self.argv or not self.argv[0]:
            raise ValueError("A process requires an executable")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("A process requires a finite positive timeout")
        if min(self.stdout_limit, self.stderr_limit, self.grace) < 0 or not math.isfinite(
            self.grace
        ):
            raise ValueError("Invalid output or termination limit")
        if (self.cancellation is CancellationPolicy.SAFE_BOUNDARY) != (
            self.safe_boundary is not None
        ):
            raise ValueError("A mutating process requires its cancellation handler")


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class ManagedProcess:
    def __init__(self, process: Process, spec: ProcessSpec) -> None:
        self.process = process
        self.spec = spec
        self.stdin = process.stdin
        self.stdout = process.stdout
        self.stderr = process.stderr
        self._closed = False
        self._lock = anyio.Lock()

    async def cancel(self) -> None:
        with anyio.CancelScope(shield=True):
            async with self._lock:
                if self._closed:
                    return
                if self.spec.safe_boundary is not None:
                    if self.process.returncode is None:
                        with suppress(ProcessLookupError):
                            await self.spec.safe_boundary(self.process)
                else:
                    with suppress(ProcessLookupError):
                        os.killpg(self.process.pid, signal.SIGTERM)
                        await anyio.sleep(self.spec.grace)
                        os.killpg(self.process.pid, signal.SIGKILL)
                await self.process.wait()
                self._closed = True


class ProcessRunner:
    def __init__(self, capacity: int = 8) -> None:
        self.limiter = anyio.CapacityLimiter(capacity)

    @asynccontextmanager
    async def open(self, spec: ProcessSpec) -> AsyncIterator[ManagedProcess]:
        async with self.limiter:
            with anyio.CancelScope(shield=True):
                child = await anyio.open_process(
                    spec.argv,
                    cwd=spec.cwd,
                    env=spec.env,
                    stdin=spec.stdin,
                    stdout=subprocess.DEVNULL if spec.discard_stdout else subprocess.PIPE,
                    stderr=subprocess.DEVNULL if spec.discard_stderr else subprocess.PIPE,
                    start_new_session=True,
                    pass_fds=spec.pass_fds,
                )
            managed = ManagedProcess(child, spec)
            try:
                with anyio.fail_after(spec.timeout):
                    yield managed
            except BaseException:
                await managed.cancel()
                raise
            finally:
                with anyio.CancelScope(shield=True):
                    if child.returncode is None:
                        await managed.cancel()
                    await child.aclose()

    async def run(
        self,
        spec: ProcessSpec,
        input_data: bytes | None = None,
        *,
        cancel: anyio.Event | None = None,
    ) -> ProcessResult:
        if input_data is not None and spec.stdin != subprocess.PIPE:
            raise ValueError("Process input requires a private pipe")
        outputs = [bytearray(), bytearray()]
        oversized = False

        async def drain(stream: ByteReceiveStream, index: int, limit: int) -> None:
            nonlocal oversized
            async for chunk in stream:
                if len(outputs[index]) + len(chunk) <= limit:
                    outputs[index].extend(chunk)
                else:
                    oversized = True

        async with self.open(spec) as managed:

            async def stop() -> None:
                assert cancel is not None
                await cancel.wait()
                await managed.cancel()

            async with anyio.create_task_group() as monitor:
                if cancel is not None:
                    monitor.start_soon(stop)
                async with anyio.create_task_group() as group:
                    if managed.stdout:
                        group.start_soon(drain, managed.stdout, 0, spec.stdout_limit)
                    if managed.stderr:
                        group.start_soon(drain, managed.stderr, 1, spec.stderr_limit)
                    if managed.stdin:
                        with suppress(anyio.BrokenResourceError):
                            if input_data is not None:
                                await managed.stdin.send(input_data)
                            await managed.stdin.aclose()
                status = await managed.process.wait()
                monitor.cancel_scope.cancel()
        if oversized:
            raise subprocess.SubprocessError("Process output exceeds its limit")
        return ProcessResult(status, bytes(outputs[0]), bytes(outputs[1]))


def _text(data: bytes) -> str:
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
    discard_stderr: bool
    cwd: str | Path | None
    max_output_bytes: int | None


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
    discard_stderr: bool = False,
    cwd: str | Path | None = None,
    max_output_bytes: int | None = None,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    """Run a synchronous plugin tool in a worker thread or standalone process."""
    if input is not None and stdin is not None:
        raise ValueError("Select one input source")
    spec = ProcessSpec(
        tuple(argv),
        cwd=Path(cwd) if cwd else None,
        env=env,
        stdin=subprocess.PIPE if input is not None else stdin,
        timeout=timeout,
        grace=termination_grace,
        discard_stdout=discard_output,
        discard_stderr=discard_output or discard_stderr,
        stdout_limit=MAX_OUTPUT if max_output_bytes is None else max_output_bytes,
        stderr_limit=MAX_OUTPUT if max_output_bytes is None else max_output_bytes,
    )

    async def execute() -> ProcessResult:
        return await ProcessRunner().run(spec, input.encode() if input is not None else None)

    try:
        try:
            anyio.from_thread.check_cancelled()
        except RuntimeError:
            response = anyio.run(execute, backend="asyncio")
        else:
            response = anyio.from_thread.run(execute)
    except TimeoutError:
        raise subprocess.TimeoutExpired(argv, timeout) from None
    result: subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]
    if text:
        result = subprocess.CompletedProcess(
            argv, response.returncode, _text(response.stdout), _text(response.stderr)
        )
    else:
        result = subprocess.CompletedProcess(
            argv, response.returncode, response.stdout, response.stderr
        )
    if check:
        result.check_returncode()
    return result
