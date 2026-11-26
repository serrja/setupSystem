"""Manage the GitHub CLI and explicitly selected account operations."""

import asyncio
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field

from setup_plugin_gh import accounts
from setup_plugin_gh.enums import AccountAction
from setup_plugin_gh.settings import Settings
from setup_system.execution.recipes import user_terminal
from setup_system.execution.recipes.apt import DebianPackages, apply_packages
from setup_system.execution.recipes.native import command
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan, Result

GH = Path("/usr/bin/gh")


class Account(accounts.Selection):
    host: str = Field(
        description="Choose the hosting service for this account operation.",
        default="github.com",
        max_length=253,
        title="Hosting service",
        json_schema_extra={"ui": {"choice_provider": "hosts"}},
    )
    account: accounts.AccountName = Field(
        description="Select the account whose session or resources this action will inspect.",
        title="Local hosting account",
        json_schema_extra={"ui": {"choice_provider": "accounts"}},
    )


class SignIn(accounts.Selection):
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


def observed_login(
    inputs: accounts.Selection,
) -> Literal["different", "review-storage", "verified"]:
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


def sign_in(inputs: accounts.Selection) -> Result:
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


def sign_out(inputs: accounts.Selection) -> Result:
    selected = accounts.selected_name(accounts.local_names(inputs.host), inputs)
    if not selected or not accounts.selector_supported():
        return Result(outcome=Outcome.BLOCKED, message="The selected account or CLI changed")
    rc, _ = command(str(GH), "auth", "logout", "--hostname", inputs.host, "--user", selected)
    return Result(
        outcome=Outcome.UNKNOWN_OUTCOME if rc else Outcome.CHANGED,
        message="Check the selected GitHub CLI account",
    )


MUTATIONS: dict[AccountAction, Callable[[accounts.Selection], Result]] = {
    AccountAction.LOGIN: sign_in,
    AccountAction.LOGOUT: sign_out,
}


class Plugin(DebianPackages):
    package_folder = Path(__file__).parent
    settings_model = Settings
    executable = str(GH)
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        LifecycleAction.CHECK: Inputs,
        AccountAction.STATUS: Account,
        AccountAction.LOGIN: SignIn,
        AccountAction.LOGOUT: Account,
    }

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id not in AccountAction:
            return super().prepare(action_id, settings, inputs)
        action = AccountAction(action_id)
        if not isinstance(settings, Settings) or not isinstance(inputs, accounts.Selection):
            raise ValueError("Invalid GitHub CLI account parameters")
        if inputs.host not in {"github.com", settings.enterprise_host}:
            return Plan(outcome=Outcome.BLOCKED, summary="Approve this enterprise host first")
        if not GH.is_file():
            return Plan(outcome=Outcome.BLOCKED, summary="Install the official GitHub CLI first")
        if action is AccountAction.LOGIN and isinstance(inputs, SignIn):
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
        if not isinstance(inputs, Account):
            raise ValueError("Select an existing local account")
        return accounts.prepare(inputs, logout=action is AccountAction.LOGOUT)

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
        action = AccountAction(action_id)
        execute = MUTATIONS.get(action)
        if (
            execute is None
            or not isinstance(inputs, accounts.Selection)
            or not isinstance(inputs, self.command(action).schema)
        ):
            raise ValueError("Select a supported GitHub CLI account action")
        return execute(inputs)

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
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
        return await super().verify(plan, settings, inputs)


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    apply_packages("gh", action_id, plan)
