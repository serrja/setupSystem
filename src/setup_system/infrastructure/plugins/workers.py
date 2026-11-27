"""Supervise one pinned worker and route its bounded protocol exchange."""

import os
import subprocess
import sys
from collections.abc import Awaitable, Callable, Iterator
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path
from uuid import UUID, uuid4

import anyio
from anyio.abc import Process
from anyio.streams.buffered import BufferedByteReceiveStream
from setup_system_sdk.context import ProviderCall
from setup_system_sdk.enums.errors import ErrorCode
from setup_system_sdk.enums.operation import CancellationPolicy, WorkerMethod, WorkerMode
from setup_system_sdk.enums.permissions import Permission
from setup_system_sdk.environment import session_environment, ssh_socket
from setup_system_sdk.operations import ActionEvent, Receipt
from setup_system_sdk.processes import ProcessRunner, ProcessSpec
from setup_system_sdk.protocol import (
    CancelMessage,
    EventMessage,
    Exchange,
    Message,
    ProviderCallMessage,
    ProviderReplyMessage,
    ReplyMessage,
    RequestMessage,
)
from setup_system_sdk.schemas.errors import Error
from setup_system_sdk.schemas.wire import LIMIT, Reply, Request

from setup_system.errors import OperationError
from setup_system.infrastructure.plugins.environments import Activation
from setup_system.infrastructure.plugins.lifecycle import generation, generation_lock
from setup_system.journal import Journal


@contextmanager
def transport_errors(operation: UUID | None) -> Iterator[None]:
    """Deliver safe protocol failures through the same boundary as worker diagnostics."""
    try:
        yield
    except (
        ExceptionGroup,
        TimeoutError,
        anyio.IncompleteRead,
        anyio.EndOfStream,
        anyio.BrokenResourceError,
        anyio.DelimiterNotFound,
    ) as failure:
        group = (
            failure if isinstance(failure, ExceptionGroup) else ExceptionGroup("worker", [failure])
        )
        diagnostic = group.subgroup(OperationError)
        if diagnostic:
            error: BaseException = diagnostic
            while isinstance(error, BaseExceptionGroup):
                error = error.exceptions[0]
            raise error from None
        timed_out = group.subgroup(TimeoutError) is not None
        raise OperationError(
            Error(
                code=ErrorCode.WORKER_TIMEOUT if timed_out else ErrorCode.WORKER_FAILED,
                message="The executor exceeded its query deadline."
                if timed_out
                else "The executor did not return a valid reply. Inspect the operation history.",
                operation_id=operation,
            )
        ) from None


class WorkerClient:
    def __init__(
        self,
        runner: ProcessRunner,
        timeout: float,
        journal: Journal | None = None,
        *,
        terminal_timeout: float = 900,
        idle_timeout: float = 90,
        grace: float = 5,
    ) -> None:
        self.runner = runner
        self.timeout = timeout
        self.journal = journal
        self.terminal_timeout = terminal_timeout
        self.idle_timeout = idle_timeout
        self.grace = grace

    async def call(
        self,
        release: Activation,
        request: Request,
        *,
        execute: bool = False,
        route: ProviderCall | None = None,
        native: Callable[[], Awaitable[Receipt]] | None = None,
        emit: Callable[[ActionEvent], Awaitable[None]] | None = None,
        cancel: anyio.Event | None = None,
    ) -> Reply:
        if execute != (request.method == WorkerMethod.EXECUTE):
            raise ValueError("The request is forbidden in this dispatch mode")
        identity = release.executor
        if request.plugin_id != identity.plugin_id or (
            request.executor and request.executor != identity
        ):
            raise ValueError("Worker identity changed")
        request = request.model_copy(
            update={"executor": identity, "operation_id": request.operation_id or uuid4()}
        )
        if execute and (
            not request.snapshot or request.snapshot.operation_id != request.operation_id
        ):
            raise ValueError("Execute requires a confirmed snapshot")
        snapshot = request.snapshot
        step = (
            next((s for s in snapshot.plan.steps if s.step_key == request.step_key), None)
            if snapshot
            else None
        )
        if execute and (step is None or step.executor != identity):
            raise ValueError("Execute requires the confirmed executor")
        guarded = bool(execute and step and step.cancellation is CancellationPolicy.SAFE_BOUNDARY)
        terminal = bool(
            execute
            and step
            and snapshot
            and request.action_id == snapshot.action_id
            and snapshot.grants_terminal(identity, step, release.manifest)
        )
        env = session_environment(release.manifest.session_fields)
        env.update(
            {
                "SETUP_SYSTEM_WORKER_QUERY_TIMEOUT": str(self.timeout),
                "SETUP_SYSTEM_WORKER_TERMINAL_TIMEOUT": str(self.terminal_timeout),
                "SETUP_SYSTEM_WORKER_GRACE": str(self.grace),
            }
        )
        if terminal:
            # Older installed workers consume this grant through their original adapter.
            env["SETUP_SYSTEM_NATIVE_TERMINAL"] = "1"
        if any(
            a.id == request.action_id and Permission.ACCOUNT_KEYS in a.permissions
            for a in release.manifest.actions
        ):
            env.update(ssh_socket())
        env.update(
            {
                name: os.environ[name]
                for name in release.manifest.environment_fields
                if name in os.environ
            }
        )
        exchange = Exchange(uuid4())
        with transport_errors(request.operation_id), ExitStack() as channels:
            reader = None
            writer = None
            if native is not None:
                if not execute:
                    raise ValueError("Native channel requested for a query")
                reader, descriptor = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
                channels.callback(os.close, reader)
                writer = channels.enter_context(os.fdopen(descriptor, "wb", buffering=0))
            write_lock = anyio.Lock()

            async def request_stop(child: Process) -> None:
                if child.stdin:
                    with (
                        anyio.move_on_after(self.grace),
                        suppress(
                            OSError,
                            ValueError,
                            anyio.BrokenResourceError,
                            anyio.ClosedResourceError,
                        ),
                    ):
                        async with write_lock:
                            await child.stdin.send(
                                exchange.encode(CancelMessage(request_id=exchange.request_id))
                            )

            async def shutdown(child: Process) -> None:
                await request_stop(child)
                if writer:
                    writer.close()
                if child.stdin:
                    await child.stdin.aclose()

                async def drain() -> None:
                    if child.stdout:
                        async for _ in child.stdout:
                            pass

                async with anyio.create_task_group() as draining:
                    draining.start_soon(drain)
                    if not guarded:
                        with anyio.move_on_after(self.grace):
                            await child.wait()
                        if child.returncode is None:
                            with suppress(ProcessLookupError):
                                child.terminate()
                    await child.wait()

            spec = ProcessSpec(
                (
                    sys.executable,
                    "-I",
                    "-B",
                    str(Path(__file__).with_name("supervise.py")),
                    str(self.grace),
                    str(reader if reader is not None else -1),
                    str(0 if guarded else os.getpid()),
                    str(release.python),
                    "-I",
                    "-B",
                    "-m",
                    "setup_system_sdk.worker",
                    WorkerMode.EXECUTE if execute else WorkerMode.QUERY,
                    *([str(reader)] if reader is not None else []),
                ),
                cwd=release.resources,
                env=env,
                stdin=subprocess.PIPE,
                timeout=None if execute else self.timeout,
                grace=self.grace,
                cancellation=CancellationPolicy.SAFE_BOUNDARY,
                safe_boundary=shutdown,
                pass_fds=() if reader is None else (reader,),
                discard_stderr=True,
            )
            with generation_lock(generation(release), exclusive=False):
                async with self.runner.open(spec) as process:
                    assert process.stdin and process.stdout
                    stream = BufferedByteReceiveStream(process.stdout)
                    output = process.stdin

                    async def send(message: Message) -> None:
                        async with write_lock:
                            await output.send(exchange.encode(message))

                    async def cancel_worker(event: anyio.Event) -> None:
                        await event.wait()
                        await request_stop(process.process)
                        if not guarded:
                            await anyio.sleep(self.grace)
                            with suppress(ProcessLookupError):
                                process.process.terminate()

                    await send(RequestMessage(request_id=exchange.request_id, payload=request))
                    async with anyio.create_task_group() as tasks:
                        if cancel:
                            tasks.start_soon(cancel_worker, cancel)
                        while True:
                            with anyio.fail_after(None if execute else self.idle_timeout):
                                frame = await stream.receive_until(b"\n", LIMIT)
                            message = exchange.decode(frame + b"\n")
                            if isinstance(message, ReplyMessage):
                                reply = message.payload
                                await output.aclose()
                                async for _ in stream:
                                    raise ValueError("Worker sent data after its reply")
                                if await process.process.wait() != 0:
                                    raise ValueError("Worker terminated before completion")
                                tasks.cancel_scope.cancel()
                                break
                            if isinstance(message, EventMessage):
                                if message.payload.message_code == "authorize":
                                    if native is None or writer is None:
                                        raise ValueError("Unapproved native authorization event")
                                    launch, native = native, None
                                    evidence = await launch()
                                    data = memoryview(evidence.model_dump_json().encode())
                                    if len(data) > LIMIT:
                                        raise ValueError("Oversized receipt")
                                    while data:
                                        await anyio.wait_writable(writer.fileno())
                                        data = data[os.write(writer.fileno(), data) :]
                                    writer.close()
                                if self.journal and request.operation_id:
                                    await self.journal.emit(
                                        request.operation_id, identity, message.payload
                                    )
                                if emit:
                                    await emit(message.payload)
                                continue
                            if not isinstance(message, ProviderCallMessage) or route is None:
                                raise ValueError("Unexpected message from the worker")
                            result = await route(message.payload.method, message.payload.request)
                            await send(
                                ProviderReplyMessage(
                                    request_id=exchange.request_id,
                                    call_id=message.call_id,
                                    payload=result,
                                )
                            )
        if reply.error:
            raise OperationError(reply.error)
        return reply
