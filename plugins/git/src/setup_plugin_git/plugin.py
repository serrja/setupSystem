"""Manage Git author identities and owned SSH keys."""

import asyncio
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from setup_plugin_git import keys, local_keys
from setup_plugin_git.enums import GitAction
from setup_plugin_git.identities import (
    Identity,
    ImportIdentity,
    Style,
    UseIdentity,
    prepare,
    publish,
    verified_identity,
)
from setup_plugin_git.settings import Settings
from setup_system.execution.recipes.apt import (
    DebianPackages,
    Packages,
    RetiredPackage,
    apply_packages,
)
from setup_system_sdk.enums.lifecycle import LifecycleAction, PackageAction
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan, Result


class CleanupKeys(Inputs):
    pass


class Plugin(DebianPackages):
    package_folder = Path(__file__).parent
    settings_model = Settings
    executable = "/usr/bin/git"
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        PackageAction.REMOVE_RETIRED: RetiredPackage,
        GitAction.UPLOAD_KEY: keys.Upload,
        GitAction.VERIFY_SSH: keys.Probe,
        GitAction.REVOKE_KEY: keys.Revoke,
        LifecycleAction.CONFIGURE: Identity,
        GitAction.CONFIGURE_STYLE: Style,
        GitAction.IMPORT_IDENTITY: ImportIdentity,
        GitAction.USE_IDENTITY: UseIdentity,
        LifecycleAction.CHECK: Inputs,
        GitAction.REMOVE_OWNED_KEYS: CleanupKeys,
        GitAction.GENERATE_KEY: local_keys.Generate,
        GitAction.REMOVE_LOCAL_KEYS: local_keys.RemoveLocal,
    }

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

    async def choices(self, provider: str, query: str, settings: PluginSettings) -> list[str]:
        if provider != "retired-packages":
            return []
        raw = await asyncio.to_thread((self.package_folder / "apt.json").read_bytes)
        return [name for name in Packages.model_validate_json(raw).retired_names if query in name]

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

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        if isinstance(inputs, local_keys.Generate):
            return await asyncio.to_thread(local_keys.verify_generation, inputs, plan)
        if isinstance(inputs, local_keys.RemoveLocal):
            absent = await asyncio.to_thread(local_keys.removed, plan)
            return Result.observed(
                outcome=Outcome.COMPLETE if absent else Outcome.PARTIAL,
                message="Checked the reviewed local key files",
                resources=plan.resources,
            )
        if isinstance(inputs, (keys.Upload, keys.Revoke)):
            return await asyncio.to_thread(keys.verify, inputs, plan)
        if isinstance(inputs, CleanupKeys):
            return await asyncio.to_thread(keys.cleanup, plan, readback=True)
        if not isinstance(inputs, (Identity, UseIdentity, ImportIdentity, Style)):
            return await super().verify(plan, settings, inputs)
        current = [edit.resource() for edit in await asyncio.to_thread(prepare, inputs)]
        verified = [(r.identity, r.before, r.permissions) for r in current] == [
            (r.identity, r.after, r.permissions) for r in plan.resources
        ]
        if verified and isinstance(inputs, UseIdentity):
            verified = await asyncio.to_thread(verified_identity, inputs)
        return Result.observed(
            outcome=Outcome.COMPLETE if verified else Outcome.PARTIAL,
            message="Git identity verified" if verified else "Git identity readback differs",
            resources=plan.resources,
        )


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    apply_packages("git", action_id, plan)
