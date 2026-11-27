"""Bounded process groups for workers, read-only probes and native tools."""

from __future__ import annotations

import math
import os
import signal
import subprocess
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from io import BytesIO, TextIOWrapper
from pathlib import Path
from typing import Literal, TypedDict, Unpack, overload

import anyio
from anyio.abc import ByteReceiveStream, Process
from anyio.lowlevel import RunVar

from setup_system_sdk.enums.operation import CancellationPolicy as CancellationPolicy

MAX_OUTPUT = 1024 * 1024


@dataclass(frozen=True)
class ProcessSpec:
    argv: tuple[str, ...]
    cwd: Path | None = None
    env: Mapping[str, str] | None = None
    stdin: int | None = subprocess.DEVNULL
    pass_fds: tuple[int, ...] = ()
    timeout: float | None = 180
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
        if self.timeout is not None and (not math.isfinite(self.timeout) or self.timeout <= 0):
            raise ValueError("A process requires a finite positive timeout")
        if min(self.stdout_limit, self.stderr_limit, self.grace) < 0 or not math.isfinite(
            self.grace
        ):
            raise ValueError("Invalid output or termination limit")
        if (self.cancellation is CancellationPolicy.SAFE_BOUNDARY) != (
            self.safe_boundary is not None
        ):
            raise ValueError("A safe boundary requires its cancellation handler")


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

            done = anyio.Event()

            async def communicate() -> None:
                # Keep draining pipes while a transaction acknowledges its safe boundary.
                with anyio.CancelScope(shield=spec.safe_boundary is not None):
                    try:
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
                        await managed.process.wait()
                    finally:
                        done.set()

            async with anyio.create_task_group() as monitor:
                monitor.start_soon(communicate)
                if cancel is not None:
                    monitor.start_soon(stop)
                try:
                    await done.wait()
                except BaseException:
                    await managed.cancel()
                    raise
                finally:
                    monitor.cancel_scope.cancel()
            status = await managed.process.wait()
        if oversized:
            raise subprocess.SubprocessError("Process output exceeds its limit")
        return ProcessResult(status, bytes(outputs[0]), bytes(outputs[1]))


@dataclass(frozen=True)
class ExecutionProcesses:
    """The process budget and cancellation grant for one worker request."""

    runner: ProcessRunner
    query_timeout: float = 180
    terminal_timeout: float = 900
    grace: float = 5
    cancelled: anyio.Event | None = None
    mutating: bool = False
    cancellation: CancellationPolicy = CancellationPolicy.TERMINATE
    native_terminal: bool = False

    def timeout(self, requested: float | None) -> float | None:
        if self.mutating:
            return requested
        return min(requested, self.query_timeout) if requested is not None else self.query_timeout


_processes: ContextVar[ExecutionProcesses | None] = ContextVar("execution_processes", default=None)


@contextmanager
def execution_processes(service: ExecutionProcesses) -> Iterator[None]:
    token = _processes.set(service)
    try:
        yield
    finally:
        _processes.reset(token)


_standalone: RunVar[ExecutionProcesses] = RunVar("standalone_processes")


def current_processes() -> ExecutionProcesses:
    service = _processes.get()
    if service is not None:
        return service
    # Async callers outside a worker share a limiter for the duration of their event loop.
    standalone = _standalone.get(None)
    if standalone is None:
        standalone = ExecutionProcesses(ProcessRunner())
        _standalone.set(standalone)
    return standalone


def native_terminal_timeout() -> float:
    service = _processes.get()
    if service is None or not service.native_terminal:
        raise ValueError("Native terminal access requires a confirmed execute grant")
    return service.terminal_timeout


def _text(data: bytes) -> str:
    with TextIOWrapper(BytesIO(data)) as stream:
        return stream.read()


class _RunOptions(TypedDict, total=False):
    timeout: float | None
    termination_grace: float | None
    cancellation: CancellationPolicy | None
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
    timeout: float | None = None,
    termination_grace: float | None = None,
    cancellation: CancellationPolicy | None = None,
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

    if input is None and stdin is None and _processes.get() is not None:
        # A worker reserves its own stdin for control frames.
        stdin = subprocess.DEVNULL

    effective_timeout = timeout

    async def execute() -> ProcessResult:
        nonlocal effective_timeout
        service = current_processes()
        stop = service.cancelled
        if stop is not None and stop.is_set():
            raise RuntimeError("The operation has been cancelled")

        async def finish_transaction(process: Process) -> None:
            await process.wait()

        guarded = service.cancellation is CancellationPolicy.SAFE_BOUNDARY
        if cancellation is CancellationPolicy.SAFE_BOUNDARY and not guarded:
            if _processes.get() is not None:
                raise ValueError("The step must confirm safe-boundary execution")
            guarded = True
        effective_timeout = None if guarded else service.timeout(timeout)
        spec = ProcessSpec(
            tuple(argv),
            cwd=Path(cwd) if cwd else None,
            env=env,
            stdin=subprocess.PIPE if input is not None else stdin,
            timeout=effective_timeout,
            grace=service.grace if termination_grace is None else termination_grace,
            cancellation=CancellationPolicy.SAFE_BOUNDARY
            if guarded
            else CancellationPolicy.TERMINATE,
            safe_boundary=finish_transaction if guarded else None,
            discard_stdout=discard_output,
            discard_stderr=discard_output or discard_stderr,
            stdout_limit=MAX_OUTPUT if max_output_bytes is None else max_output_bytes,
            stderr_limit=MAX_OUTPUT if max_output_bytes is None else max_output_bytes,
        )
        return await service.runner.run(
            spec, input.encode() if input is not None else None, cancel=stop
        )

    try:
        try:
            anyio.from_thread.check_cancelled()
        except RuntimeError:
            response = anyio.run(execute, backend="asyncio")
        else:
            response = anyio.from_thread.run(execute)
    except TimeoutError:
        raise subprocess.TimeoutExpired(argv, effective_timeout or 0) from None
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
