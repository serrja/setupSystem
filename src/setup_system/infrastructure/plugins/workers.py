"""Supervise one pinned worker and route its bounded protocol exchange."""

import os
import subprocess
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from uuid import uuid4

import anyio
from anyio.streams.buffered import BufferedByteReceiveStream
from setup_system_sdk.context import ProviderCall
from setup_system_sdk.enums.operation import WorkerMethod
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
from setup_system_sdk.schemas.wire import LIMIT, Reply, Request

from setup_system.errors import OperationError
from setup_system.infrastructure.plugins.environments import Activation
from setup_system.infrastructure.plugins.lifecycle import generation, generation_lock
from setup_system.journal import Journal


class WorkerClient:
    def __init__(
        self, runner: ProcessRunner, timeout: float, journal: Journal | None = None
    ) -> None:
        self.runner = runner
        self.timeout = timeout
        self.journal = journal

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
        env = session_environment()
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
        with ExitStack() as channels:
            reader = None
            writer = None
            if native is not None:
                if not execute:
                    raise ValueError("Native channel requested for a query")
                reader, descriptor = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
                channels.callback(os.close, reader)
                writer = channels.enter_context(os.fdopen(descriptor, "wb", buffering=0))
            spec = ProcessSpec(
                (
                    str(release.python),
                    "-I",
                    "-B",
                    "-m",
                    "setup_system_sdk.worker",
                    "execute" if execute else "query",
                    *([str(reader)] if reader is not None else []),
                ),
                cwd=release.resources,
                env=env,
                stdin=subprocess.PIPE,
                timeout=self.timeout,
                pass_fds=() if reader is None else (reader,),
                discard_stderr=True,
            )
            with generation_lock(generation(release), exclusive=False):
                async with self.runner.open(spec) as process:
                    assert process.stdin and process.stdout
                    stream = BufferedByteReceiveStream(process.stdout)
                    output = process.stdin
                    lock = anyio.Lock()

                    async def send(message: Message) -> None:
                        async with lock:
                            await output.send(exchange.encode(message))

                    async def cancel_worker(event: anyio.Event) -> None:
                        await event.wait()
                        await send(CancelMessage(request_id=exchange.request_id))
                        guarded = request.snapshot and any(
                            s.step_key == request.step_key and s.cancellation == "safe_boundary"
                            for s in request.snapshot.plan.steps
                        )
                        if not guarded:
                            await anyio.sleep(0.25)
                            await process.cancel()

                    await send(RequestMessage(request_id=exchange.request_id, payload=request))
                    async with anyio.create_task_group() as tasks:
                        if cancel:
                            tasks.start_soon(cancel_worker, cancel)
                        while True:
                            frame = await stream.receive_until(b"\n", LIMIT)
                            message = exchange.decode(frame + b"\n")
                            if isinstance(message, ReplyMessage):
                                reply = message.payload
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
                    await output.aclose()
                    if await process.process.wait() != 0:
                        raise ValueError("Worker terminated before completion")
        if reply.error:
            raise OperationError(reply.error)
        return reply
