"""Manage Git author identities and owned SSH keys."""

import json
from importlib.resources import files as package_files
from pathlib import Path

from setup_system_sdk.blocking import run_sync
from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.files.backend import NativeBackend
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan, Result

from setup_plugin_git import keys, local_keys
from setup_plugin_git.identities import (
    Identity,
    ImportIdentity,
    Style,
    UseIdentity,
    prepare,
    publish,
    verified_identity,
)
from setup_plugin_git.models import CleanupKeys, Settings


class PluginActions(NativeBackend):
    package_folder = Path(str(package_files("setup_plugin_git").joinpath("resources")))

    executable = "/usr/bin/git"

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if isinstance(inputs, local_keys.Generate):
            return local_keys.generate_plan(inputs)
        if isinstance(inputs, local_keys.RemoveLocal):
            return local_keys.removal_plan()
        if isinstance(inputs, (keys.Upload, keys.Revoke, keys.Probe)) and isinstance(
            settings, Settings
        ):
            if inputs.host not in {"github.com", settings.enterprise_host}:
                return Plan(
                    outcome=Outcome.BLOCKED, summary="Approve this enterprise host in Settings"
                )
            if isinstance(inputs, keys.Probe):
                return keys.access(inputs)
            return keys.prepare(inputs)
        if isinstance(inputs, CleanupKeys) and isinstance(settings, Settings):
            return keys.cleanup_plan({"github.com", settings.enterprise_host})
        if isinstance(inputs, (Identity, UseIdentity, ImportIdentity, Style)):
            resources = [edit.resource() for edit in prepare(inputs)]
            changed = any(r.before != r.after for r in resources)
            return Plan(
                summary=f"Review {action_id} for the displayed Git configuration files",
                outcome=Outcome.READY if changed else Outcome.NO_CHANGES,
                resources=resources,
            )
        return super().prepare(action_id, settings, inputs)

    async def choices(
        self,
        provider: str,
        query: str,
        settings: PluginSettings,
        context: PluginContext | None = None,
    ) -> list[str]:
        if provider != "retired-packages":
            return []
        raw = await run_sync((self.package_folder / "apt.json").read_bytes)
        return [name for name in json.loads(raw).get("retired_names", []) if query in name]

    def mutate(
        self, action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan
    ) -> Result:
        if isinstance(inputs, local_keys.Generate):
            return local_keys.generate(inputs, plan)
        if isinstance(inputs, local_keys.RemoveLocal):
            return local_keys.remove(plan)
        if isinstance(inputs, (keys.Upload, keys.Revoke)):
            return keys.mutate(inputs, plan)
        if isinstance(inputs, CleanupKeys):
            return keys.cleanup(plan)
        if not isinstance(inputs, (Identity, UseIdentity, ImportIdentity, Style)):
            raise ValueError("Unknown Git mutation")
        edits = prepare(inputs)
        if [edit.resource() for edit in edits] != plan.resources:
            return Result(outcome=Outcome.BLOCKED, message="Git files changed after confirmation")
        backup = publish(edits)
        return Result(
            outcome=Outcome.CHANGED,
            message="Identity published",
            resources=plan.resources,
            recovery_id=backup.name,
        )

    async def verify(
        self,
        plan: Plan,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Result:
        if isinstance(inputs, local_keys.Generate):
            return await run_sync(local_keys.verify_generation, inputs, plan)
        if isinstance(inputs, local_keys.RemoveLocal):
            absent = await run_sync(local_keys.removed, plan)
            return Result.observed(
                outcome=Outcome.COMPLETE if absent else Outcome.PARTIAL,
                message="Checked the reviewed local key files",
                resources=plan.resources,
            )
        if isinstance(inputs, (keys.Upload, keys.Revoke)):
            return await run_sync(keys.verify, inputs, plan)
        if isinstance(inputs, CleanupKeys):
            return await run_sync(keys.cleanup, plan, readback=True)
        if not isinstance(inputs, (Identity, UseIdentity, ImportIdentity, Style)):
            return await super().verify(plan, settings, inputs)
        current = [edit.resource() for edit in await run_sync(prepare, inputs)]
        verified = [(r.identity, r.before, r.permissions) for r in current] == [
            (r.identity, r.after, r.permissions) for r in plan.resources
        ]
        if verified and isinstance(inputs, UseIdentity):
            verified = await run_sync(verified_identity, inputs)
        return Result.observed(
            outcome=Outcome.COMPLETE if verified else Outcome.PARTIAL,
            message="Git identity verified" if verified else "Git identity readback differs",
            resources=plan.resources,
        )
