"""Manage snapd independently of the applications that use it."""

import asyncio
import re
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

import httpx

from setup_system_sdk import (
    Inputs,
    LifecycleAction,
    Outcome,
    Plan,
    PluginSettings,
    Resource,
    Result,
)
from setup_system_sdk.apt import DebianPackages, ServiceAction, apply_packages, installed
from setup_system_sdk.native import command

from .removal_guard import require_empty

SNAP = "/usr/bin/snap"


class SnapAction(StrEnum):
    LIST_INSTALLED = "list-installed"


def inventory() -> list[Resource]:
    rc, output = command(SNAP, "list", "--all", "--color=never", "--unicode=never", capture=True)
    if rc:
        raise ValueError("The snapd daemon did not return its installed inventory")
    lines = output.strip().splitlines()
    if not lines or (len(lines) == 1 and lines[0].startswith("No snaps are installed yet")):
        return []
    if lines[0].split() != ["Name", "Version", "Rev", "Tracking", "Publisher", "Notes"]:
        raise ValueError("Unknown snap list format")
    resources = []
    for line in lines[1:]:
        fields = line.split()
        if len(fields) != 6:
            raise ValueError("Incomplete snap inventory")
        name, version, revision, channel, _, notes = fields
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,80}", name) or not re.fullmatch(
            r"x?[0-9]+", revision
        ):
            raise ValueError("Unknown snap identity")
        fingerprint = f"{version} · {channel} · {notes}"
        resources.append(
            Resource(
                kind="snap-revision",
                identity=f"{name}@{revision}",
                ownership="preexisting",
                before=fingerprint,
                after=fingerprint,
            )
        )
    return resources


def inspection() -> Result:
    try:
        rows = inventory()
    except (OSError, ValueError):
        return Result(
            outcome=Outcome.NEEDS_USER_ACTION,
            message="Install snapd and start its system service to inspect installed snaps.",
        )
    return Result(
        outcome=Outcome.NO_CHANGES,
        message=f"{len(rows)} installed snap revision(s). Open List installed snaps for details.",
        resources=rows,
    )


class Plugin(DebianPackages):
    settings_model = PluginSettings
    executable = SNAP
    package_folder = Path(__file__).parent
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        SnapAction.LIST_INSTALLED: Inputs,
        LifecycleAction.CHECK: Inputs,
        ServiceAction.START: Inputs,
    }

    async def inspect(self, settings: PluginSettings) -> Result:
        return await asyncio.to_thread(inspection)

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id in {SnapAction.LIST_INSTALLED, LifecycleAction.CHECK}:
            result = inspection()
            return Plan(summary=result.message, outcome=result.outcome, resources=result.resources)
        if action_id == LifecycleAction.UNINSTALL and installed("snapd"):
            observed = inspection()
            try:
                if observed.outcome != Outcome.NO_CHANGES or observed.resources:
                    raise ValueError("Existing snap inventory")
                require_empty()
            except (httpx.HTTPError, ValueError, OSError):
                return Plan(
                    outcome=Outcome.BLOCKED,
                    summary="Review existing snaps, saved snapshots and active snapd changes.",
                    resources=observed.resources,
                )
        return super().prepare(action_id, settings, inputs)


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    if action_id == LifecycleAction.UNINSTALL:
        require_empty()
    apply_packages("snap", action_id, plan)
