"""Execute one bounded, versioned command in the activated extension interpreter."""

import asyncio
import sys
from importlib import metadata
from uuid import UUID

from pydantic import ValidationError

from setup_system_sdk import PLUGIN_GROUP
from setup_system_sdk.enums.errors import ErrorCode
from setup_system_sdk.enums.operation import WorkerMethod
from setup_system_sdk.enums.ui import StorageScope
from setup_system_sdk.plugin import Backend, PluginSettings, ownership_context
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
    if manifest.id != plugin_id or manifest.entry_point != entry.value:
        raise ContractError(
            ErrorCode.INCOMPATIBLE_PLUGIN, "Extension metadata and entry point differ."
        )
    implementation = entry.load()
    if not isinstance(implementation, type) or not issubclass(implementation, Backend):
        raise ContractError(ErrorCode.INCOMPATIBLE_PLUGIN, "The extension backend is incompatible.")
    backend = implementation()
    if (
        manifest.environment_fields != backend.settings_model.environment_fields
        or manifest.settings_schema != form_schema(backend.settings_model, StorageScope.SETTINGS)
        or manifest.input_schemas
        != {
            key: form_schema(command.schema, StorageScope.ACTION)
            for key, command in backend.commands().items()
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
                await check_choices(backend, settings, item, field)
            if item is None or not hints or not hints.choice_provider or hints.allow_custom:
                continue
            if (
                item
                not in (await backend.choices(hints.choice_provider, str(item), settings))[:128]
            ):
                raise ContractError(
                    ErrorCode.UNAVAILABLE_CHOICE, "Select an available choice.", field
                )


async def dispatch(request: Request) -> Reply:
    backend, manifest = load_backend(request.plugin_id)
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
            await check_choices(backend, settings, settings)
        if request.method is WorkerMethod.VALIDATE:
            return Reply(settings=settings.model_dump(mode="json"))
        if request.method is WorkerMethod.INSPECT:
            return Reply(result=await backend.inspect(settings))
        if request.method is WorkerMethod.CHOICES and request.provider:
            choices = await backend.choices(request.provider, request.query, settings)
            return Reply(choices=tuple(item for item in choices[:128] if len(item) <= 512))
        if request.action_id is None or request.action_id not in manifest.input_schemas:
            raise ContractError(
                ErrorCode.UNKNOWN_ACTION, "Select an action from the active release."
            )
        command = backend.command(request.action_id)
        try:
            inputs = command.validate(request.inputs)
        except ValidationError as exc:
            raise invalid_field(exc, manifest.input_schemas[command.id]) from None
        if request.method is WorkerMethod.PLAN:
            await check_choices(backend, settings, inputs)
            return Reply(
                plan=await command.plan(settings, inputs),
                settings=settings.model_dump(mode="json"),
            )
        if request.method is WorkerMethod.VERIFY and request.plan is not None:
            return Reply(result=await command.readback(request.plan, settings, inputs))
        raise ContractError(ErrorCode.INVALID_REQUEST, "The action request is incomplete.")


def main() -> int:
    operation_id: UUID | None = None
    try:
        raw = sys.stdin.buffer.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ContractError(ErrorCode.INVALID_REQUEST, "The request exceeds the worker limit.")
        try:
            request = Request.model_validate_json(raw)
        except ValidationError:
            raise ContractError(
                ErrorCode.INVALID_REQUEST, "The request has an incompatible wire format."
            ) from None
        operation_id = request.operation_id
        reply = asyncio.run(dispatch(request))
        encoded = reply.model_dump_json().encode()
        if len(encoded) > LIMIT:
            raise ContractError(ErrorCode.WORKER_FAILED, "The response exceeds the worker limit.")
    except ContractError as exc:
        encoded = (
            Reply(
                error=Error(
                    code=exc.code, message=exc.message, field=exc.field, operation_id=operation_id
                )
            )
            .model_dump_json()
            .encode()
        )
    except Exception:
        encoded = (
            Reply(
                error=Error(
                    code=ErrorCode.WORKER_FAILED,
                    operation_id=operation_id,
                    message="Check the activated release and required native programs.",
                )
            )
            .model_dump_json()
            .encode()
        )
    sys.stdout.buffer.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
