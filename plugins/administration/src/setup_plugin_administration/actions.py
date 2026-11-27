"""Manage password-authenticated sudo access for an existing Debian login."""

from __future__ import annotations

from pathlib import Path

from setup_system_sdk.blocking import run_sync
from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.resources import ResourceOwnership
from setup_system_sdk.plugin import Backend, PluginSettings
from setup_system_sdk.providers import CapabilityRequest
from setup_system_sdk.schemas.common import Inputs, Values
from setup_system_sdk.schemas.operation import Plan, Resource, Result

from setup_plugin_administration.enums import PluginAction
from setup_plugin_administration.models import Login

from . import grant as native
from .account import read_account
from .models import AdminIntent


async def platform_facts(context: PluginContext | None, detail: str = "target") -> Values:
    if context is None:
        raise ValueError("The action requires a selected platform provider")
    return await context.inspect_provider(
        CapabilityRequest(
            capability="system.platform", step_key="platform", payload={"detail": detail}
        )
    )


def local_logins(supported: bool) -> list[str]:
    if not supported:
        return []
    names = []
    for record in Path("/etc/passwd").read_text().splitlines():
        name = record.partition(":")[0]
        try:
            read_account(AdminIntent(username=name))
        except (ValueError, OSError, KeyError):
            continue
        names.append(name)
    return sorted(names)


def grant_plan(login: Login, boot: str, *, check: bool = False) -> Plan:
    account = read_account(AdminIntent(username=login.username))
    if check:
        identity = native.fingerprint(account, boot)
        return Plan(
            outcome=Outcome.READY if account.member else Outcome.BLOCKED,
            summary=f"Verify the effective sudo policy for {account.username} (UID {account.uid}).",
            resources=[
                Resource(
                    kind="policy-check",
                    identity=account.username,
                    before=identity,
                    after=identity,
                    ownership=ResourceOwnership.PREEXISTING,
                )
            ],
        )
    resource = Resource(
        kind="group-membership",
        identity=f"sudo/{account.username}",
        ownership=ResourceOwnership.MODIFIED,
        before=native.fingerprint(account, boot),
        after=native.fingerprint(account.validated(member=True), boot),
    )
    return Plan(
        outcome=Outcome.NO_CHANGES if account.member else Outcome.READY,
        summary=(
            f"{account.username} already belongs to sudo. Select Verify effective access."
            if account.member
            else f"Grant {account.username} (UID {account.uid}) full sudo access."
        ),
        warnings=["A new login is required. Removing this extension preserves the grant."],
        resources=[resource],
    )


class PluginActions(Backend):
    async def choices(
        self,
        provider: str,
        query: str,
        settings: PluginSettings,
        context: PluginContext | None = None,
    ) -> list[str]:
        if provider != "accounts":
            return []
        return [
            n
            for n in await run_sync(
                local_logins, (await platform_facts(context)).get("supported") is True
            )
            if query.casefold() in n.casefold()
        ][:100]

    async def inspect(
        self, settings: PluginSettings, context: PluginContext | None = None
    ) -> Result:
        accounts = await run_sync(
            local_logins, (await platform_facts(context)).get("supported") is True
        )
        return Result(
            outcome=Outcome.READY if accounts else Outcome.BLOCKED,
            message=f"{len(accounts)} eligible local logins.",
        )

    async def plan(
        self,
        action_id: str,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Plan:
        if action_id not in (PluginAction.GRANT_SUDO, PluginAction.VERIFY_SUDO) or not isinstance(
            inputs, Login
        ):
            raise ValueError("Choose an existing login for the sudo grant.")
        facts = await platform_facts(context)
        boot = facts.get("boot")
        if facts["supported"] is not True or not isinstance(boot, str):
            return Plan(
                outcome=Outcome.BLOCKED,
                summary="Sudo onboarding requires a supported Debian target.",
            )
        return await run_sync(grant_plan, inputs, boot, check=action_id == PluginAction.VERIFY_SUDO)

    async def verify(
        self,
        plan: Plan,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Result:
        if not isinstance(inputs, Login):
            raise ValueError("Expected a confirmed login.")
        facts = await platform_facts(context)
        boot = facts.get("boot")
        if facts["supported"] is not True or not isinstance(boot, str):
            return Result(
                outcome=Outcome.UNKNOWN_OUTCOME, message="Platform identity is unavailable."
            )
        account = await run_sync(read_account, AdminIntent(username=inputs.username))
        if not account.member or native.fingerprint(account, boot) != plan.resources[0].after:
            return Result(
                outcome=Outcome.PARTIAL,
                message="Membership or account identity differs from the confirmation.",
            )
        if plan.resources[0].kind == "policy-check":
            return Result(
                outcome=Outcome.NEEDS_USER_ACTION,
                message="Authenticate again to verify the effective sudo policy for this login.",
            )
        return Result.observed(
            outcome=Outcome.CHANGED,
            message=(
                "Verified the login's group membership. Start a new session, then select "
                "Verify effective access to check its current sudo policy."
            ),
            resources=plan.resources,
        )


def apply_native(
    action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan
) -> Result | None:
    from setup_plugin_administration.adapters.native import run_native_play

    if action_id not in (PluginAction.GRANT_SUDO, PluginAction.VERIFY_SUDO) or not isinstance(
        inputs, Login
    ):
        raise ValueError("Unknown administrative action.")
    resource = plan.resources[0]
    run_native_play(
        "administration",
        "verify.yml" if action_id == PluginAction.VERIFY_SUDO else "grant.yml",
        {
            "username": inputs.username,
            "before_digest": resource.before,
            "after_digest": resource.after,
        },
        "su",
    )
    if action_id == PluginAction.VERIFY_SUDO:
        account = native.observe(inputs.username)
        if not account.member or native.fingerprint(account) != resource.after:
            return Result(outcome=Outcome.PARTIAL, message="The verified login identity changed.")
        return Result(
            outcome=Outcome.COMPLETE,
            message="Effective sudo policy and login identity verified.",
            resources=[resource.model_copy(update={"verified": True})],
        )
    return None
