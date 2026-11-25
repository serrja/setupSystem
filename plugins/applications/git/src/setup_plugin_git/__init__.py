"""Personal Git profiles and native GitHub CLI account inspection."""

import asyncio
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field

from setup_plugin_git import accounts, keys, local_keys
from setup_plugin_git.profiles import (
    ImportProfile,
    Profile,
    Style,
    UseProfile,
    prepare,
    publish,
    verified_identity,
)
from setup_system_sdk import Inputs, Outcome, Plan, PluginSettings, Result, user_terminal
from setup_system_sdk.apt import DebianPackages, apply_packages
from setup_system_sdk.native import command

GH = Path("/usr/bin/gh")


class Settings(PluginSettings):
    enterprise_host: str = Field(
        default="",
        max_length=253,
        pattern=r"^$|^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]([a-z0-9-]*[a-z0-9])?$",
        title="Approved enterprise host",
        description="Enter the exact GitHub Enterprise DNS hostname you intend to use.",
    )


class Account(keys.KeyAccount):
    host: str = Field(
        default="github.com",
        max_length=253,
        title="Hosting service",
        json_schema_extra={"ui": {"choice_provider": "hosts"}},
    )
    account: keys.AccountName = Field(
        title="Local hosting account", json_schema_extra={"ui": {"choice_provider": "accounts"}}
    )


class SignIn(keys.KeyAccount):
    """Read back the account selected for the native browser flow."""


def unlocked_store() -> bool:
    try:
        rc, locked = command(
            "/usr/bin/gdbus",
            "call",
            "--session",
            "--dest",
            "org.freedesktop.secrets",
            "--object-path",
            "/org/freedesktop/secrets/aliases/default",
            "--method",
            "org.freedesktop.DBus.Properties.Get",
            "org.freedesktop.Secret.Collection",
            "Locked",
            capture=True,
        )
    except FileNotFoundError:
        return False
    return rc == 0 and locked.strip() == "(<false>,)"


STORE_REVIEW = "Account verified. Run gh auth status to check its displayed credential source."


def observed_login(inputs: SignIn) -> Literal["different", "review-storage", "verified"]:
    rc, help_text = command(str(GH), "auth", "status", "--help", capture=True)
    flags = help_text.split()
    if rc or "--json" not in flags:
        # The stable Debian client exposes account identity through the public user API.
        rc, name = command(
            str(GH), "api", "--hostname", inputs.host, "user", "--jq", ".login", capture=True
        )
        if not rc and name.strip().casefold() == inputs.account.casefold():
            return "review-storage"
        return "different"
    probe = [str(GH), "auth", "status", "--active", "--hostname", inputs.host]
    if "--no-refresh" in flags:
        probe.append("--no-refresh")
    rc, fields = command(
        *probe,
        "--json",
        "hosts",
        "--jq",
        '.hosts[][] | select(.active and .state == "success") '
        '| [.login, (.tokenSource == "keyring")] | @tsv',
        capture=True,
    )
    account, _, secure = fields.strip().partition("\t")
    if rc or account.casefold() != inputs.account.casefold():
        return "different"
    return "verified" if secure == "true" else "review-storage"


class CleanupKeys(Inputs):
    pass


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
        "login": SignIn,
        "logout": Account,
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
        if isinstance(inputs, Account) and isinstance(settings, Settings):
            if inputs.host not in {"github.com", settings.enterprise_host}:
                return Plan(outcome=Outcome.BLOCKED, summary="Approve the enterprise host first")
            if not GH.is_file():
                return Plan(outcome=Outcome.BLOCKED, summary="Install the official GitHub CLI")
            return accounts.prepare(inputs, logout=action_id == "logout")
        if isinstance(inputs, SignIn) and isinstance(settings, Settings):
            if inputs.host not in {"github.com", settings.enterprise_host}:
                return Plan(outcome=Outcome.BLOCKED, summary="Approve this enterprise host first")
            if not GH.is_file():
                return Plan(
                    outcome=Outcome.BLOCKED, summary="Install the official GitHub CLI first"
                )
            observed = observed_login(inputs)
            if observed == "review-storage":
                return Plan(outcome=Outcome.NEEDS_USER_ACTION, summary=STORE_REVIEW)
            if observed == "verified":
                return Plan(outcome=Outcome.NO_CHANGES, summary="Selected GitHub account is active")
            if not unlocked_store():
                return Plan(
                    outcome=Outcome.NEEDS_USER_ACTION,
                    summary="Unlock the default Secret Service collection before signing in.",
                )
            if not user_terminal.available():
                return Plan(
                    outcome=Outcome.NEEDS_USER_ACTION,
                    summary=(
                        f"Run gh auth login --web --skip-ssh-key --hostname {inputs.host} "
                        f"in your terminal. Choose {inputs.account} in the browser."
                    ),
                )
            return Plan(
                summary=f"Sign into {inputs.host} as {inputs.account} in GitHub CLI",
                warnings=[
                    "GitHub CLI may write its token to hosts.yml if the keyring fails during "
                    "sign-in. The action checks the credential source when the terminal closes."
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
        return super().prepare(action_id, settings, inputs)

    async def choices(self, provider: str, query: str, settings: PluginSettings) -> list[str]:
        if not isinstance(settings, Settings):
            return []
        hosts = sorted({"github.com", settings.enterprise_host} - {""})
        if provider == "hosts":
            return [host for host in hosts if query.casefold() in host]
        if provider != "accounts":
            return []
        names: set[str] = set()
        for host in hosts:
            names.update(await asyncio.to_thread(accounts.local_names, host))
        return [name for name in sorted(names) if query.casefold() in name.casefold()][:128]

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
        if isinstance(inputs, SignIn) and action_id == "login":
            rc = user_terminal.run_native(
                [str(GH), "auth", "login", "--web", "--skip-ssh-key", "--hostname", inputs.host]
            )
            observed = observed_login(inputs)
            if observed == "review-storage":
                return Result(outcome=Outcome.NEEDS_USER_ACTION, message=STORE_REVIEW)
            active = observed == "verified"
            return Result(
                outcome=Outcome.CHANGED if rc == 0 and active else Outcome.NEEDS_USER_ACTION,
                message=(
                    "Selected GitHub account is active"
                    if rc == 0 and active
                    else "Check gh auth status for the selected account and its credential source"
                ),
            )
        if isinstance(inputs, Account) and action_id == "logout":
            selected = accounts.selected_name(accounts.local_names(inputs.host), inputs)
            if not selected or not accounts.selector_supported():
                return Result(
                    outcome=Outcome.BLOCKED, message="The selected account or CLI changed"
                )
            rc, _ = command(
                str(GH), "auth", "logout", "--hostname", inputs.host, "--user", selected
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
        if isinstance(inputs, SignIn):
            observed = await asyncio.to_thread(observed_login, inputs)
            if observed == "review-storage":
                return Result(outcome=Outcome.NEEDS_USER_ACTION, message=STORE_REVIEW)
            active = observed == "verified"
            return Result(
                outcome=Outcome.COMPLETE if active else Outcome.NEEDS_USER_ACTION,
                message="Selected GitHub account is active"
                if active
                else "Check the active account",
            )
        if isinstance(inputs, Account):
            return await asyncio.to_thread(accounts.verify_logout, inputs)
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
