"""Manage Git author profiles and owned SSH keys."""

import asyncio
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from pydantic import Field

from setup_plugin_git import keys, local_keys
from setup_plugin_git.profiles import (
    ImportProfile,
    Profile,
    Style,
    UseProfile,
    prepare,
    publish,
    verified_identity,
)
from setup_system_sdk import Inputs, LifecycleAction, Outcome, Plan, PluginSettings, Result
from setup_system_sdk.apt import (
    DebianPackages,
    PackageAction,
    Packages,
    RetiredPackage,
    apply_packages,
)


class GitAction(StrEnum):
    CONFIGURE_STYLE = "configure-style"
    IMPORT_PROFILE = "import-profile"
    USE_PROFILE = "use-profile"
    UPLOAD_KEY = "upload-key"
    VERIFY_SSH = "verify-ssh"
    REVOKE_KEY = "revoke-key"
    REMOVE_OWNED_KEYS = "remove-owned-keys"
    GENERATE_KEY = "generate-key"
    REMOVE_LOCAL_KEYS = "remove-local-keys"


class Settings(PluginSettings):
    enterprise_host: str = Field(
        default="",
        max_length=253,
        pattern=r"^$|^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]([a-z0-9-]*[a-z0-9])?$",
        title="Approved enterprise host",
        description="Enter the exact GitHub Enterprise DNS hostname you intend to use.",
    )


class CleanupKeys(Inputs):
    pass


class Plugin(DebianPackages):
    package_folder = Path(__file__).parent
    settings_model = Settings
    executable = "/usr/bin/git"
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        PackageAction.REMOVE_RETIRED: RetiredPackage,
        GitAction.UPLOAD_KEY: keys.Upload,
        GitAction.VERIFY_SSH: keys.Probe,
        GitAction.REVOKE_KEY: keys.Revoke,
        LifecycleAction.CONFIGURE: Profile,
        GitAction.CONFIGURE_STYLE: Style,
        GitAction.IMPORT_PROFILE: ImportProfile,
        GitAction.USE_PROFILE: UseProfile,
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
        if isinstance(inputs, (Profile, UseProfile, ImportProfile, Style)):
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
        if not isinstance(inputs, (Profile, UseProfile, ImportProfile, Style)):
            raise ValueError("Unknown Git mutation")
        edits = prepare(inputs)
        if [edit.resource() for edit in edits] != plan.resources:
            return Result(outcome=Outcome.BLOCKED, message="Git files changed after confirmation")
        backup = publish(edits)
        return Result(
            outcome=Outcome.CHANGED,
            message="Profile published",
            resources=plan.resources,
            recovery_id=backup.name,
        )

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        if isinstance(inputs, local_keys.Generate):
            return await asyncio.to_thread(local_keys.verify_generation, inputs, plan)
        if isinstance(inputs, local_keys.RemoveLocal):
            absent = await asyncio.to_thread(local_keys.removed, plan)
            return Result(
                outcome=Outcome.COMPLETE if absent else Outcome.PARTIAL,
                message="Checked the reviewed local key files",
                resources=plan.resources,
            )
        if isinstance(inputs, (keys.Upload, keys.Revoke)):
            return await asyncio.to_thread(keys.verify, inputs, plan)
        if isinstance(inputs, CleanupKeys):
            return await asyncio.to_thread(keys.cleanup, plan, readback=True)
        if not isinstance(inputs, (Profile, UseProfile, ImportProfile, Style)):
            return await super().verify(plan, settings, inputs)
        current = [edit.resource() for edit in await asyncio.to_thread(prepare, inputs)]
        verified = [(r.identity, r.before) for r in current] == [
            (r.identity, r.after) for r in plan.resources
        ]
        if verified and isinstance(inputs, UseProfile):
            verified = await asyncio.to_thread(verified_identity, inputs)
        return Result(
            outcome=Outcome.COMPLETE if verified else Outcome.PARTIAL,
            message="Git profile verified" if verified else "Git profile readback differs",
            resources=plan.resources,
        )


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    apply_packages("git", action_id, plan)
