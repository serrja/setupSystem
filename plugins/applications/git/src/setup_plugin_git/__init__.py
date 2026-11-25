"""Personal Git profiles and native GitHub CLI account inspection."""

import asyncio
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
from setup_system_sdk import Inputs, Outcome, Plan, PluginSettings, Result
from setup_system_sdk.apt import DebianPackages, apply_packages
from setup_system_sdk.native import command


class Settings(PluginSettings):
    enterprise_host: str = Field(
        default="",
        max_length=253,
        pattern=r"^$|^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]([a-z0-9-]*[a-z0-9])?$",
        title="Approved enterprise host",
        description="Enter the exact GitHub Enterprise DNS hostname you intend to use.",
    )


class Account(Inputs):
    host: str = Field(
        default="github.com", min_length=1, max_length=253, title="Git hosting service"
    )


class CleanupKeys(Inputs):
    pass


def logins(host: str) -> list[str]:
    rc, names = command(
        "/usr/bin/gh",
        "auth",
        "status",
        "--hostname",
        host,
        "--json",
        "hosts",
        "--jq",
        ".hosts[][] | .login",
        capture=True,
    )
    if rc:
        raise ValueError("GitHub CLI cannot read the selected account inventory")
    return [name.casefold() for name in names.splitlines() if name]


class Plugin(DebianPackages):
    package_folder = Path(__file__).parent
    settings_model = Settings
    executable = "/usr/bin/git"
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        "upload-key": keys.Upload,
        "verify-ssh": keys.Probe,
        "revoke-key": keys.Revoke,
        "configure": Profile,
        "configure-style": Style,
        "import-profile": ImportProfile,
        "use-profile": UseProfile,
        "check": Inputs,
        "status": Account,
        "login": Account,
        "logout": keys.KeyAccount,
        "remove-owned-keys": CleanupKeys,
        "generate-key": local_keys.Generate,
        "remove-local-keys": local_keys.RemoveLocal,
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
            return keys.access(inputs) if isinstance(inputs, keys.Probe) else keys.prepare(inputs)
        if isinstance(inputs, CleanupKeys) and isinstance(settings, Settings):
            return keys.cleanup_plan({"github.com", settings.enterprise_host})
        if (
            isinstance(inputs, keys.KeyAccount)
            and action_id == "logout"
            and isinstance(settings, Settings)
        ):
            if inputs.host not in {"github.com", settings.enterprise_host}:
                return Plan(outcome=Outcome.BLOCKED, summary="Approve the enterprise host first")
            present = inputs.account.casefold() in logins(inputs.host)
            return Plan(
                outcome=Outcome.READY if present else Outcome.NO_CHANGES,
                summary=f"Sign out {inputs.account} on {inputs.host} in the local CLI",
                warnings=[
                    "Existing author profiles and remote OAuth authorizations stay available."
                ],
            )
        if isinstance(inputs, (Profile, UseProfile, ImportProfile, Style)):
            resources = [edit.resource() for edit in prepare(inputs)]
            changed = any(r.before != r.after for r in resources)
            return Plan(
                summary=f"Review {action_id} for the displayed Git configuration files",
                outcome=Outcome.READY if changed else Outcome.NO_CHANGES,
                resources=resources,
            )
        if isinstance(inputs, Account) and isinstance(settings, Settings):
            if inputs.host not in {"github.com", settings.enterprise_host}:
                return Plan(
                    outcome=Outcome.BLOCKED,
                    summary="Approve this enterprise host in Settings",
                    resources=[],
                )
            if not Path("/usr/bin/gh").is_file():
                return Plan(
                    outcome=Outcome.BLOCKED,
                    summary="Install the official GitHub CLI first",
                    resources=[],
                )
            if action_id == "status":
                rc, _ = command(
                    "/usr/bin/gh", "auth", "status", "--active", "--hostname", inputs.host
                )
                return Plan(
                    outcome=Outcome.NO_CHANGES if rc == 0 else Outcome.NEEDS_USER_ACTION,
                    summary="Active account verified"
                    if rc == 0
                    else "Check network access and gh authentication",
                    resources=[],
                )
            if action_id == "login":
                return Plan(
                    outcome=Outcome.NEEDS_USER_ACTION,
                    resources=[],
                    summary=(
                        "In your terminal run gh auth login --web --skip-ssh-key "
                        f"--hostname {inputs.host}. "
                        "Complete browser authentication, then choose Account status."
                    ),
                )
        return super().prepare(action_id, settings, inputs)

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
        if isinstance(inputs, keys.KeyAccount) and action_id == "logout":
            rc, _ = command(
                "/usr/bin/gh", "auth", "logout", "--hostname", inputs.host, "--user", inputs.account
            )
            return Result(
                outcome=Outcome.UNKNOWN_OUTCOME if rc else Outcome.CHANGED,
                message="Check the selected GitHub CLI account",
            )
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
        if isinstance(inputs, keys.KeyAccount):
            accounts = await asyncio.to_thread(logins, inputs.host)
            return Result(
                outcome=Outcome.PARTIAL
                if inputs.account.casefold() in accounts
                else Outcome.COMPLETE,
                message="Checked the selected GitHub CLI account after logout",
            )
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
