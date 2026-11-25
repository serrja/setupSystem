"""Dispatch one validated request inside the activated plugin's interpreter."""

import asyncio
import sys
from importlib import metadata, resources

from setup_system_sdk import (
    PLUGIN_GROUP,
    Backend,
    Inputs,
    Manifest,
    PluginSettings,
    ownership_context,
)
from setup_system_sdk.protocol import LIMIT, Reply, Request


async def dispatch(request: Request) -> Reply:
    with ownership_context(request.holdings):
        return await _dispatch(request)


def load_backend(plugin_id: str) -> tuple[Backend, Manifest]:
    matches = metadata.entry_points(group=PLUGIN_GROUP, name=plugin_id)
    if len(matches) != 1:
        raise ValueError("Missing or ambiguous plugin")
    entry = next(iter(matches))
    implementation = entry.load()
    if not isinstance(implementation, type) or not issubclass(implementation, Backend):
        raise ValueError("Invalid plugin backend")
    backend = implementation()
    manifest = Manifest.model_validate_json(
        resources.files(entry.module).joinpath("manifest.json").read_bytes()
    )
    if (
        manifest.entry_point != entry.value
        or manifest.id != plugin_id
        or manifest.environment_fields != backend.settings_model.environment_fields
        or manifest.settings_schema != backend.settings_model.model_json_schema()
        or manifest.input_schemas
        != {key: model.model_json_schema() for key, model in backend.inputs.items()}
    ):
        raise ValueError("Installed schema differs from the reviewed plugin")
    return backend, manifest


async def check_choices(
    backend: Backend, settings: PluginSettings, model: Inputs | PluginSettings
) -> None:
    for name, field in type(model).model_fields.items():
        extra = field.json_schema_extra
        ui = extra.get("ui") if isinstance(extra, dict) else None
        if not isinstance(ui, dict) or ui.get("allow_custom") or not ui.get("choice_provider"):
            continue
        value = getattr(model, name)
        if value is None:
            continue
        for item in value if isinstance(value, (tuple, list)) else (value,):
            provider = ui["choice_provider"]
            if not isinstance(provider, str) or item not in await backend.choices(
                provider, str(item), settings
            ):
                raise ValueError("The selected choice is no longer available")


async def _dispatch(request: Request) -> Reply:
    backend, _ = load_backend(request.plugin_id)
    if request.method == "migrate":
        values = backend.settings_model.migrate(request.saved, request.settings_version)
        backend.settings_model.model_validate(values)
        return Reply(settings=values)
    settings = backend.settings_model.resolve(
        profile=request.profile, saved=request.saved, overrides=request.overrides
    )
    values = settings.model_dump(mode="json")
    if request.method in ("validate", "plan"):
        await check_choices(backend, settings, settings)
    if request.method == "validate":
        return Reply(settings=values)
    if request.method == "inspect":
        return Reply(result=await backend.inspect(settings))
    if request.method == "choices" and request.provider:
        choices = await backend.choices(request.provider, request.query, settings)
        return Reply(choices=tuple(item for item in choices[:128] if len(item) <= 512))
    if request.action_id is None:
        raise ValueError("Missing action")
    inputs = backend.inputs[request.action_id].model_validate(request.inputs)
    if request.method == "plan":
        await check_choices(backend, settings, inputs)
        return Reply(plan=await backend.plan(request.action_id, settings, inputs), settings=values)
    if request.method == "verify" and request.plan is not None:
        return Reply(result=await backend.verify(request.plan, settings, inputs))
    raise ValueError("Invalid operation")


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(LIMIT + 1)
        if len(raw) > LIMIT:
            return 2
        reply = asyncio.run(dispatch(Request.model_validate_json(raw))).model_dump_json().encode()
        if len(reply) > LIMIT:
            return 2
        sys.stdout.buffer.write(reply)
    except Exception:
        # Exceptions and validation input from extensions can include credentials.
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
