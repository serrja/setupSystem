"""Manage password-authenticated sudo access for an existing Debian login."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import ClassVar

from pydantic import Field, field_validator

from setup_system.infrastructure.native_target import current_target
from setup_system_sdk import Backend, Inputs, Outcome, Plan, PluginSettings, Resource, Result

from . import grant as native
from .account import read_account
from .models import AdminIntent


class Login(Inputs):
    username: str = Field(
        title="Existing local login",
        min_length=1,
        max_length=32,
        pattern=r"^[a-z_][a-z0-9_-]{0,31}$",
        description="An ordinary account in the local passwd and group databases.",
        json_schema_extra={
            "ui": {"widget": "select", "choice_provider": "accounts", "allow_custom": False}
        },
    )

    @field_validator("username")
    @classmethod
    def ordinary(cls, name: str) -> str:
        if name == "root":
            raise ValueError("Select an ordinary interactive login.")
        return name


def local_logins() -> list[str]:
    if not current_target()[1]:
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


def grant_plan(login: Login, *, check: bool = False) -> Plan:
    account = native.observe(login.username)
    if check:
        identity = native.fingerprint(account)
        return Plan(
            outcome=Outcome.READY if account.member else Outcome.BLOCKED,
            summary=f"Verify the effective sudo policy for {account.username} (UID {account.uid}).",
            resources=[
                Resource(
                    kind="policy-check",
                    identity=account.username,
                    before=identity,
                    after=identity,
                    ownership="preexisting",
                )
            ],
        )
    resource = Resource(
        kind="group-membership",
        identity=f"sudo/{account.username}",
        ownership="modified",
        before=native.fingerprint(account),
        after=native.fingerprint(account.validated(member=True)),
    )
    return Plan(
        outcome=Outcome("no_changes" if account.member else "ready"),
        summary=(
            f"{account.username} already belongs to sudo. Select Verify effective access."
            if account.member
            else f"Grant {account.username} (UID {account.uid}) full sudo access."
        ),
        warnings=["A new login is required. Removing this extension preserves the grant."],
        resources=[resource],
    )


class Plugin(Backend):
    settings_model = PluginSettings
    inputs: ClassVar[dict[str, type[Inputs]]] = {"grant-sudo": Login, "verify-sudo": Login}

    async def choices(self, provider: str, query: str, settings: PluginSettings) -> list[str]:
        if provider != "accounts":
            return []
        return [
            n for n in await asyncio.to_thread(local_logins) if query.casefold() in n.casefold()
        ][:100]

    async def inspect(self, settings: PluginSettings) -> Result:
        accounts = await asyncio.to_thread(local_logins)
        return Result(
            outcome=Outcome("ready" if accounts else "blocked"),
            message=f"{len(accounts)} eligible local logins.",
        )

    async def plan(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id not in ("grant-sudo", "verify-sudo") or not isinstance(inputs, Login):
            raise ValueError("Choose an existing login for the sudo grant.")
        return await asyncio.to_thread(grant_plan, inputs, check=action_id == "verify-sudo")

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        if not isinstance(inputs, Login):
            raise ValueError("Expected a confirmed login.")
        account = await asyncio.to_thread(native.observe, inputs.username)
        if not account.member or native.fingerprint(account) != plan.resources[0].after:
            return Result(
                outcome=Outcome("partial"),
                message="Membership or account identity differs from the confirmation.",
            )
        if plan.resources[0].kind == "policy-check":
            return Result(
                outcome=Outcome.NEEDS_USER_ACTION,
                message="Authenticate again to verify the effective sudo policy for this login.",
            )
        return Result(
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
    from setup_system_sdk.native_play import run_native_play

    if action_id not in ("grant-sudo", "verify-sudo") or not isinstance(inputs, Login):
        raise ValueError("Unknown administrative action.")
    resource = plan.resources[0]
    run_native_play(
        "administration",
        "verify.yml" if action_id == "verify-sudo" else "grant.yml",
        {
            "username": inputs.username,
            "before_digest": resource.before,
            "after_digest": resource.after,
        },
        "su",
    )
    if action_id == "verify-sudo":
        account = native.observe(inputs.username)
        if not account.member or native.fingerprint(account) != resource.after:
            return Result(outcome=Outcome.PARTIAL, message="The verified login identity changed.")
        return Result(
            outcome=Outcome.COMPLETE,
            message="Effective sudo policy and login identity verified.",
            resources=[resource.model_copy(update={"verified": True})],
        )
    return None
