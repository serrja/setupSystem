"""Synthetic extension with dynamic action choices for distribution boundary checks."""

from typing import ClassVar

from pydantic import Field

from setup_system_sdk import Backend, Inputs, Outcome, Plan, PluginSettings, Result, UIHints


class Preferences(PluginSettings):
    count: int = Field(default=1, ge=1, le=10)
    environment_fields: ClassVar[dict[str, str]] = {"SETUP_PLUGIN_REVIEWKIT_COUNT": "count"}


class Select(Inputs):
    item: str | None = Field(
        default=None,
        json_schema_extra={"ui": UIHints(choice_provider="items").model_dump(mode="json")},
    )


class Toy(Backend):
    settings_model = Preferences
    inputs: ClassVar[dict[str, type[Inputs]]] = {"select": Select}

    async def choices(self, provider, query, settings):
        return ["existing", "x" * 513, *[str(i) for i in range(150)]] if provider == "items" else []

    async def plan(self, action_id, settings, inputs):
        return Plan(summary="Preview choice")

    async def inspect(self, settings):
        return Result(outcome=Outcome.READY, message="Observed fixture")

    async def verify(self, plan, settings, inputs):
        return Result(outcome=Outcome.NO_CHANGES, message="Inspected fixture")
