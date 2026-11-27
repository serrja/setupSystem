"""Execute one bounded, versioned command in the activated extension interpreter."""

import argparse
import os
from dataclasses import replace
from importlib import metadata
from uuid import uuid4

import anyio
from pydantic import ValidationError

from setup_system_sdk import PLUGIN_GROUP, ActionEvent, StepPlan
from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.errors import ErrorCode
from setup_system_sdk.enums.operation import (
    CancellationPolicy,
    EventPhase,
    ProviderMethod,
    WorkerMethod,
    WorkerMode,
)
from setup_system_sdk.enums.ui import StorageScope
from setup_system_sdk.environment import declare_session_fields
from setup_system_sdk.operations import Receipt
from setup_system_sdk.plugin import Backend, PluginSettings, ownership_context
from setup_system_sdk.processes import (
    ExecutionProcesses,
    ProcessRunner,
    current_processes,
    execution_processes,
)
from setup_system_sdk.protocol import (
    MESSAGES,
    CancelMessage,
    EventMessage,
    Exchange,
    Message,
    ProviderCallMessage,
    ProviderPayload,
    ProviderReplyMessage,
    ReplyMessage,
    RequestMessage,
)
from setup_system_sdk.providers import CapabilityRequest
from setup_system_sdk.schemas.common import Inputs, Values
from setup_system_sdk.schemas.errors import Error
from setup_system_sdk.schemas.forms import form_schema
from setup_system_sdk.schemas.manifest import Manifest, UIHints
from setup_system_sdk.schemas.wire import LIMIT, Reply, Request


class ContractError(Exception):
    def __init__(self, code: ErrorCode, message: str, field: tuple[str | int, ...] = ()) -> None:
        self.code = code
        self.message = message
        self.field = field


def load_backend(plugin_id: str) -> tuple[Backend, Manifest]:
    entries = tuple(metadata.entry_points(group=PLUGIN_GROUP, name=plugin_id))
    if len(entries) != 1 or entries[0].dist is None:
        raise ContractError(
            ErrorCode.INCOMPATIBLE_PLUGIN, "Activate the reviewed extension release."
        )
    entry = entries[0]
    package = entry.module.split(".")[0]
    assert entry.dist is not None
    source = entry.dist.locate_file(f"{package}/manifest.json")
    manifest = Manifest.model_validate_json(source.read_text())
    if (
        manifest.id != plugin_id
        or manifest.entry_point != entry.value
        or manifest.version != entry.dist.version
    ):
        raise ContractError(
            ErrorCode.INCOMPATIBLE_PLUGIN, "Extension metadata and entry point differ."
        )
    implementation = entry.load()
    if not isinstance(implementation, type) or not issubclass(implementation, Backend):
        raise ContractError(ErrorCode.INCOMPATIBLE_PLUGIN, "The extension backend is incompatible.")
    backend = implementation()
    commands = backend.commands()
    provided = {c.id: c.request_schema for c in manifest.provides}
    if provided != {
        name: handler.request_model.model_json_schema()
        for name, handler in backend.providers.items()
    }:
        raise ContractError(
            ErrorCode.INCOMPATIBLE_PLUGIN, "Provider contracts differ from the manifest"
        )

    if (
        any(key != command.id.value for key, command in commands.items())
        or manifest.settings_version != backend.settings_model.schema_version
        or manifest.environment_fields != backend.settings_model.environment_fields
        or manifest.settings_schema != form_schema(backend.settings_model, StorageScope.SETTINGS)
        or manifest.input_schemas
        != {
            key: form_schema(command.schema, StorageScope.ACTION)
            for key, command in commands.items()
        }
    ):
        raise ContractError(
            ErrorCode.INCOMPATIBLE_PLUGIN, "Activate a release with matching parameter schemas."
        )
    return backend, manifest


def invalid_field(error: ValidationError, schema: Values) -> ContractError:
    detail = error.errors(include_url=False, include_input=False, include_context=False)[0]
    names: set[str] = set()

    def collect(node: Values) -> None:
        properties = node.get("properties")
        if isinstance(properties, dict):
            names.update(properties)
        for value in node.values():
            if isinstance(value, dict):
                collect(value)

    collect(schema)
    field = tuple(k for k in detail["loc"] if isinstance(k, int) or k in names)
    messages = {
        "missing": "Enter a value for the required field.",
        "extra_forbidden": "Remove the unrecognized field.",
        "literal_error": "Select a value from the available choices.",
        "enum": "Select a value from the available choices.",
    }
    return ContractError(
        ErrorCode.INVALID_INPUT,
        messages.get(detail["type"], "Check the value against the field requirements."),
        field,
    )


async def check_choices(
    backend: Backend,
    settings: PluginSettings,
    model: Inputs | PluginSettings,
    context: PluginContext,
    path: tuple[str | int, ...] = (),
) -> None:
    for name, descriptor in type(model).model_fields.items():
        extra = descriptor.json_schema_extra
        metadata = extra.get("ui") if isinstance(extra, dict) else None
        hints = UIHints.model_validate(metadata) if metadata else None
        value = getattr(model, name)
        values = enumerate(value) if isinstance(value, (tuple, list)) else [(None, value)]
        for index, item in values:
            field = (*path, name) if index is None else (*path, name, index)
            if isinstance(item, (Inputs, PluginSettings)):
                await check_choices(backend, settings, item, context, field)
            if item is None or not hints or not hints.choice_provider or hints.allow_custom:
                continue
            if (
                item
                not in (await backend.choices(hints.choice_provider, str(item), settings, context))[
                    :128
                ]
            ):
                raise ContractError(
                    ErrorCode.UNAVAILABLE_CHOICE, "Select an available choice.", field
                )


async def dispatch(request: Request, mode: WorkerMode, context: PluginContext) -> Reply:
    mode = WorkerMode(mode)
    if (mode is WorkerMode.EXECUTE) != (request.method == WorkerMethod.EXECUTE):
        raise ContractError(ErrorCode.INVALID_REQUEST, "Invalid dispatch mode")
    backend, manifest = load_backend(request.plugin_id)
    declare_session_fields(manifest.session_fields)
    if request.executor != context.executor or (manifest.id, manifest.version) != (
        context.executor.plugin_id,
        context.executor.version,
    ):
        raise ContractError(ErrorCode.INCOMPATIBLE_PLUGIN, "Executor identity changed")
    if request.method is WorkerMethod.RESTORE_PLAN:
        from setup_system_sdk.files.rollback import plan as restore_plan

        source = request.snapshot
        evidence = request.receipt
        if not source or not evidence or source.executor != context.executor:
            raise ValueError("Missing original executor or receipt")
        original = next(s for s in source.plan.steps if s.step_key == request.step_key)
        evidence.validate_for(source.operation_id, original)
        if original.executor != context.executor or not any(
            a.id == source.action_id and a.supports_rollback for a in manifest.actions
        ):
            raise ValueError("This executor has no declared rollback")
        return Reply(step=await anyio.to_thread.run_sync(restore_plan, original, evidence))
    if request.method in (WorkerMethod.EXECUTE, WorkerMethod.READBACK):
        snapshot = request.snapshot
        if not snapshot or snapshot.operation_id != request.operation_id:
            raise ContractError(ErrorCode.INVALID_REQUEST, "A confirmed operation is required")
        step = next((s for s in snapshot.plan.steps if s.step_key == request.step_key), None)
        if step is None or step.executor != context.executor:
            raise ContractError(ErrorCode.INVALID_REQUEST, "Unknown confirmed step")
        if snapshot.rollback_of is not None:
            from functools import partial

            from setup_system_sdk.files.rollback import restore_step

            if (
                step.handler != "restore-files"
                or step.capability
                or snapshot.executor != context.executor
            ):
                raise ValueError("Unexpected rollback executor")
            if request.receipt:
                request.receipt.validate_for(snapshot.operation_id, step)
            apply = request.method is WorkerMethod.EXECUTE
            result = await anyio.to_thread.run_sync(partial(restore_step, step, apply=apply))
            if apply:
                return Reply(
                    receipt=Receipt(
                        operation_id=snapshot.operation_id,
                        step_key=step.step_key,
                        executor=step.executor,
                        result=result,
                    )
                )
            return Reply(result=result)
        handler = backend.providers[step.capability] if step.capability else backend
        if step.capability:
            backend.providers[step.capability].request_model.model_validate(step.payload)
        elif snapshot.executor != step.executor or step.handler not in backend.commands():
            raise ContractError(ErrorCode.UNKNOWN_ACTION, "Unknown local step handler")
        if step.capability or request.method is WorkerMethod.EXECUTE:
            context = replace(context, provider_call=None)
        with ownership_context(snapshot.holdings):
            if request.method == WorkerMethod.EXECUTE:
                service = replace(
                    context.processes or current_processes(),
                    native_terminal=request.action_id == snapshot.action_id
                    and snapshot.grants_terminal(context.executor, step, manifest),
                )
                with execution_processes(service):
                    receipt = await handler.execute(step, replace(context, processes=service))
                receipt.validate_for(snapshot.operation_id, step)
                return Reply(receipt=receipt)
            if request.receipt:
                request.receipt.validate_for(snapshot.operation_id, step)
            return Reply(result=await handler.readback(step, request.receipt, context))
    if request.method in (WorkerMethod.PROVIDER_INSPECT, WorkerMethod.PROVIDER_PLAN):
        cap = request.capability_request
        if cap is None or cap.capability not in backend.providers:
            raise ContractError(ErrorCode.INVALID_REQUEST, "Invalid provider call")
        provider = backend.providers[cap.capability]
        provider.request_model.model_validate(cap.payload)
        context = replace(context, provider_call=None)
        with ownership_context(request.holdings):
            if request.method == WorkerMethod.PROVIDER_PLAN:
                return Reply(step=await provider.plan(cap, context))
            return Reply(facts=await provider.inspect(cap, context))

    if request.method is WorkerMethod.MIGRATE:
        migrated = backend.settings_model.migrate(request.saved, request.settings_version)
        backend.settings_model.model_validate(migrated)
        return Reply(settings=migrated)
    try:
        settings = backend.settings_model.resolve(saved=request.saved, overrides=request.overrides)
    except ValidationError as exc:
        raise invalid_field(exc, manifest.settings_schema) from None
    with ownership_context(request.holdings):
        if request.method in (WorkerMethod.PLAN, WorkerMethod.VALIDATE):
            await check_choices(backend, settings, settings, context)
        if request.method is WorkerMethod.VALIDATE:
            return Reply(settings=settings.model_dump(mode="json"))
        if request.method is WorkerMethod.INSPECT:
            return Reply(result=await backend.inspect(settings, context))
        if request.method is WorkerMethod.CHOICES and request.provider:
            choices = await backend.choices(request.provider, request.query, settings, context)
            return Reply(choices=tuple(item for item in choices[:128] if len(item) <= 512))
        if request.action_id is None or request.action_id not in manifest.input_schemas:
            raise ContractError(
                ErrorCode.UNKNOWN_ACTION, "Select an action from the active release."
            )
        context = replace(
            context, action=next(a for a in manifest.actions if a.id == request.action_id)
        )
        command = backend.command(request.action_id)
        try:
            inputs = command.validate(request.inputs)
        except ValidationError as exc:
            raise invalid_field(exc, manifest.input_schemas[command.id]) from None
        if request.method is WorkerMethod.PLAN:
            await check_choices(backend, settings, inputs, context)
            return Reply(
                plan=await backend.bind_plan(command, settings, inputs, context),
                settings=settings.model_dump(mode="json"),
            )
        if request.method is WorkerMethod.VERIFY and request.plan is not None:
            return Reply(
                result=await backend.confirm_result(
                    command, request.plan, settings, inputs, context
                )
            )
        raise ContractError(ErrorCode.INVALID_REQUEST, "The action request is incomplete.")


async def serve(mode: WorkerMode, output: int, native_fd: int | None = None) -> None:
    """Keep stdout reserved for bounded protocol frames even when a backend prints."""
    mode = WorkerMode(mode)
    pending = bytearray()

    async def line() -> bytes:
        while True:
            pos = pending.find(b"\n")
            if pos >= 0:
                raw = bytes(pending[: pos + 1])
                del pending[: pos + 1]
                return raw
            if len(pending) >= LIMIT:
                raise ValueError("Oversized worker request")
            await anyio.wait_readable(0)
            block = os.read(0, min(16384, LIMIT - len(pending)))
            if not block:
                raise EOFError
            pending.extend(block)

    raw = await line()
    first = MESSAGES.validate_json(raw)
    if not isinstance(first, RequestMessage):
        raise ValueError("Missing request")
    exchange = Exchange(first.request_id)
    exchange.decode(raw)
    request = first.payload
    cancelled = anyio.Event()
    lock = anyio.Lock()
    write_lock = anyio.Lock()

    async def send(message: Message) -> None:
        async with write_lock:
            frame = memoryview(exchange.encode(message))
            while frame:
                await anyio.wait_writable(output)
                frame = frame[os.write(output, frame) :]

    async def emit(event: ActionEvent) -> None:
        await send(EventMessage(request_id=first.request_id, payload=event))

    async def native() -> Receipt:
        if native_fd is None or mode is not WorkerMode.EXECUTE:
            raise ValueError("Missing native execution grant")
        await emit(ActionEvent(phase=EventPhase.PREPARED, message_code="authorize"))
        data = bytearray()
        while len(data) <= LIMIT:
            await anyio.wait_readable(native_fd)
            block = os.read(native_fd, min(LIMIT + 1 - len(data), 16384))
            if not block:
                return Receipt.model_validate_json(data)
            data.extend(block)
        raise ValueError("Native receipt exceeds its bound")

    outgoing, incoming = anyio.create_memory_object_stream[ProviderReplyMessage](1)

    step = (
        next(
            (s for s in request.snapshot.plan.steps if s.step_key == request.step_key),
            None,
        )
        if request.snapshot
        else None
    )
    mutating = mode is WorkerMode.EXECUTE and request.method is WorkerMethod.EXECUTE
    policy = step.cancellation if mutating and step else CancellationPolicy.TERMINATE
    scope = anyio.CancelScope()
    os.environ.pop("SETUP_SYSTEM_NATIVE_TERMINAL", None)
    processes = ExecutionProcesses(
        ProcessRunner(),
        query_timeout=float(os.environ.pop("SETUP_SYSTEM_WORKER_QUERY_TIMEOUT", "180")),
        terminal_timeout=float(os.environ.pop("SETUP_SYSTEM_WORKER_TERMINAL_TIMEOUT", "900")),
        grace=float(os.environ.pop("SETUP_SYSTEM_WORKER_GRACE", "5")),
        cancelled=cancelled,
        mutating=mutating,
        cancellation=policy,
    )

    def stop() -> None:
        cancelled.set()
        if policy is CancellationPolicy.TERMINATE:
            scope.cancel()

    async def receive() -> None:
        try:
            while True:
                message = exchange.decode(await line())
                if isinstance(message, CancelMessage):
                    stop()
                elif isinstance(message, ProviderReplyMessage):
                    await outgoing.send(message)
                else:
                    raise ValueError("Unexpected worker message")
        except EOFError:
            stop()

    async def call(method: ProviderMethod, cap: CapabilityRequest) -> Values | StepPlan:
        async with lock:
            identifier = uuid4()
            await send(
                ProviderCallMessage(
                    request_id=first.request_id,
                    call_id=identifier,
                    payload=ProviderPayload(method=method, request=cap),
                )
            )
            response = await incoming.receive()
            if response.call_id != identifier:
                raise ValueError("Invalid provider reply")
            if method is ProviderMethod.PLAN:
                return StepPlan.model_validate(response.payload)
            if isinstance(response.payload, StepPlan):
                raise ValueError("Expected provider facts")
            return response.payload

    async with incoming, outgoing, anyio.create_task_group() as tasks:
        tasks.start_soon(receive)
        try:
            if request.executor is None or request.operation_id is None:
                raise ContractError(ErrorCode.INVALID_REQUEST, "Operation identity required")
            context = PluginContext(
                request.operation_id,
                request.executor,
                cancelled,
                emit,
                call,
                native_call=native if native_fd is not None else None,
                processes=processes,
            )
            with execution_processes(processes), scope:
                reply = await dispatch(request, mode, context)
            if scope.cancel_called:
                raise ContractError(
                    ErrorCode.UNKNOWN_OUTCOME,
                    "Execution stopped. Inspect the operation before retrying.",
                )
        except ContractError as exc:
            reply = Reply(
                error=Error(
                    code=exc.code,
                    message=exc.message,
                    field=exc.field,
                    operation_id=request.operation_id,
                )
            )
        except Exception:
            reply = Reply(
                error=Error(
                    code=ErrorCode.WORKER_FAILED,
                    message="Worker could not complete the declared method",
                    operation_id=request.operation_id,
                )
            )
        await send(ReplyMessage(request_id=first.request_id, payload=reply))
        tasks.cancel_scope.cancel()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=tuple(WorkerMode))
    parser.add_argument("native_fd", nargs="?")
    arguments = parser.parse_args()
    if os.geteuid() == 0:
        return 2
    native_fd = (
        int(arguments.native_fd)
        if arguments.native_fd is not None and arguments.mode == WorkerMode.EXECUTE
        else None
    )
    output = os.dup(1)
    os.dup2(2, 1)
    os.set_blocking(output, False)
    try:
        anyio.run(serve, WorkerMode(arguments.mode), output, native_fd, backend="asyncio")
    finally:
        os.close(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
