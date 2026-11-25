"""Explicit optional package removals on supported Debian workstations."""

from __future__ import annotations

import asyncio
import os
from typing import ClassVar

from platformdirs import user_config_path
from pydantic import Field, field_validator

from setup_system_sdk import Backend, Inputs, Outcome, Plan, PluginSettings, Resource, Result
from setup_system_sdk.native_play import run_native_play

from . import native


class Candidates(Inputs):
    packages: list[str] = Field(
        title="Removal candidates",
        min_length=1,
        max_length=64,
        description="Select each package included in the confirmed APT removal closure.",
        json_schema_extra={
            "ui": {
                "widget": "selection_list",
                "choice_provider": "candidates",
                "allow_custom": False,
            }
        },
    )

    @field_validator("packages")
    @classmethod
    def reviewed(cls, names: list[str]) -> list[str]:
        if len(set(names)) != len(names) or not set(names) <= native.catalog().keys():
            raise ValueError("Select distinct candidates from the plugin catalog.")
        return names


class PurgeCandidates(Candidates):
    """Require absence of package configuration during purge readback."""


def mode(action: str) -> native.Mode:
    if action == "remove":
        return "remove"
    if action == "purge":
        return "purge"
    if action == "autoremove":
        return "autoremove"
    raise ValueError("Unsupported cleanup operation.")


def selection(action: str, inputs: Inputs) -> tuple[list[native.Package], list[str]]:
    if not isinstance(inputs, Candidates):
        raise ValueError("Select reviewed cleanup candidates.")
    packages = native.preview(mode(action), inputs.packages, os.getuid(), user_config_path())
    return packages, inputs.packages


def prepare(action: str, inputs: Inputs) -> Plan:
    packages, _ = selection(action, inputs)
    group = native.digest(packages)
    catalog = native.catalog()
    return Plan(
        outcome=Outcome.READY if packages else Outcome.NO_CHANGES,
        summary=f"{action.capitalize()} {len(packages)} package(s) from the displayed APT closure.",
        warnings=[
            "Debian removals have no automatic rollback.",
            *(f"{p.name}: {catalog[p.name].reason}" for p in packages),
        ],
        resources=[
            Resource(
                kind="package",
                identity=p.name,
                ownership="preexisting",
                before=f"{p.architecture}:{p.version}:{group}",
                after=None,
                effect="remove",
            )
            for p in packages
        ],
    )


class Plugin(Backend):
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        "remove": Candidates,
        "purge": PurgeCandidates,
        "autoremove": Candidates,
    }

    async def choices(self, provider: str, query: str, settings: PluginSettings) -> list[str]:
        if provider != "candidates":
            return []
        catalog = await asyncio.to_thread(native.catalog)
        return [name for name in sorted(catalog) if query.casefold() in name][:100]

    async def inspect(self, settings: PluginSettings) -> Result:
        def inspect_candidates() -> Result:
            arch, supported = native.current_target()
            if not supported:
                return Result(
                    outcome=Outcome.BLOCKED, message="Package cleanup requires Debian 13."
                )
            statuses = native.inventory(list(native.catalog()), "remove", arch)
            return Result(
                outcome=Outcome.READY,
                message="Installed optional packages: " + ", ".join(p.name for p in statuses),
            )

        return await asyncio.to_thread(inspect_candidates)

    async def plan(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        return await asyncio.to_thread(prepare, action_id, inputs)

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        if not isinstance(inputs, Candidates):
            raise ValueError("Expected a removal selection.")
        actual = await asyncio.to_thread(
            native.inventory,
            inputs.packages,
            "purge" if isinstance(inputs, PurgeCandidates) else "remove",
            native.current_target()[0],
        )
        if actual:
            return Result(
                outcome=Outcome.PARTIAL,
                message="Some reviewed packages still have the selected state.",
            )
        return Result(
            outcome=Outcome.COMPLETE,
            message="dpkg confirms the selected removals.",
            resources=plan.resources,
        )


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    packages, selected = selection(action_id, inputs)
    expected = native.digest(packages)
    if not packages or not all(
        r.before and r.before.endswith(":" + expected) for r in plan.resources
    ):
        raise ValueError("The package closure changed.")
    payload = native.Confirmation(
        mode=mode(action_id),
        selected=selected,
        caller_uid=os.getuid(),
        config_home=str(user_config_path()),
        expected=expected,
    )
    run_native_play("cleanup", "clean.yml", payload.model_dump(mode="json"), "sudo")
