import os
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from typing import ClassVar, Self

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.resources import ResourceEffect
from setup_system_sdk.schemas.common import ActionId, Inputs, Values
from setup_system_sdk.schemas.operation import Plan, Resource, Result


class PluginSettings(BaseSettings):
    """Resolve one current configuration from the sources accepted by the plugin."""

    model_config = SettingsConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True, env_file=None, secrets_dir=None
    )
    environment_fields: ClassVar[dict[str, str]] = {}
    schema_version: ClassVar[int] = 1

    @classmethod
    def migrate(cls, values: Values, previous_version: int) -> Values:
        if previous_version != cls.schema_version:
            raise ValueError("This settings version requires a plugin migration")
        return values

    @classmethod
    def resolve(cls, *, saved: Values, overrides: Values) -> Self:
        environment = {
            field: os.environ[name]
            for name, field in cls.environment_fields.items()
            if name in os.environ
        }
        return cls.model_validate({**saved, **environment, **overrides})

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


_ownership: ContextVar[tuple[Resource, ...]] = ContextVar("owned_resources", default=())


def owned_resources() -> tuple[Resource, ...]:
    return _ownership.get()


@contextmanager
def ownership_context(resources: tuple[Resource, ...]) -> Iterator[None]:
    token = _ownership.set(resources)
    try:
        yield
    finally:
        _ownership.reset(token)


@dataclass(frozen=True)
class Command:
    """The action key and input contract registered by the active backend."""

    id: StrEnum
    schema: type[Inputs]
    plan: Callable[[PluginSettings, Inputs], Awaitable[Plan]]
    observe: Callable[[Plan, PluginSettings, Inputs], Awaitable[Result]]

    def validate(self, values: Values) -> Inputs:
        return self.schema.model_validate(values)

    async def readback(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        """Bind successful plugin readback to the exact set of confirmed resources."""
        result = await self.observe(plan, settings, inputs)
        complete = result.outcome in {
            Outcome.COMPLETE,
            Outcome.CHANGED,
            Outcome.NO_CHANGES,
            Outcome.NEEDS_REBOOT,
        }
        expected = {(r.kind, r.identity): r for r in plan.resources}
        observed = {(r.kind, r.identity): r for r in result.resources}
        if (
            len(expected) != len(plan.resources)
            or len(observed) != len(result.resources)
            or observed.keys() - expected.keys()
            or (complete and observed.keys() != expected.keys())
        ):
            return Result(
                outcome=Outcome.PARTIAL, message="Readback omitted or repeated a resource."
            )
        for key, row in observed.items():
            approved = expected[key]
            if not row.verified and complete:
                return Result(
                    outcome=Outcome.PARTIAL, message="The handler did not verify every resource."
                )
            if not row.verified:
                continue
            if (
                (
                    row.ownership,
                    row.effect,
                    row.cleanup,
                    row.consumers,
                    row.source_sha256,
                    row.before,
                    row.before_permissions,
                )
                != (
                    approved.ownership,
                    approved.effect,
                    approved.cleanup,
                    approved.consumers,
                    approved.source_sha256,
                    approved.before,
                    approved.before_permissions,
                )
                or (approved.after is not None and row.after != approved.after)
                or (approved.effect is ResourceEffect.REMOVE and row.after is not None)
                or (approved.remote_id is not None and row.remote_id != approved.remote_id)
                or (
                    approved.permissions is not None
                    and row.permissions != approved.permissions
                    and complete
                )
            ):
                return Result(
                    outcome=Outcome.PARTIAL, message="Readback differs from the confirmation."
                )
        return result


class Backend:
    """Commands advertised by a plugin require matching implementations."""

    settings_model: ClassVar[type[PluginSettings]] = PluginSettings
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {}

    def commands(self) -> dict[str, Command]:
        """Register concrete handlers with the action model accepted by each command."""
        return {
            key.value: Command(key, schema, partial(self.plan, key), self.verify)
            for key, schema in self.action_inputs.items()
        }

    def command(self, action_id: ActionId) -> Command:
        return self.commands()[action_id]

    async def inspect(self, settings: PluginSettings) -> Result:
        raise NotImplementedError

    async def plan(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        raise NotImplementedError

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        raise NotImplementedError

    async def choices(self, provider: str, query: str, settings: PluginSettings) -> list[str]:
        return []
