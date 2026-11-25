"""Compose a reviewed cleanup from package actions, receipts and exact user-owned resources."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, SecretStr

from setup_system.application.rollback import RecoveryPlan
from setup_system.authorization import BecomePassword
from setup_system.configuration import Settings
from setup_system.configuration.store import forget_profile, profiles
from setup_system.extension_lifecycle import removable, retire
from setup_system.journal import Event
from setup_system.plugin_manager import PluginManager, Snapshot
from setup_system_sdk import Action, Identifier, Inputs, Outcome, Plan, Resource, Result, Values
from setup_system_sdk.secrets import forget_secret, secret_exists, store_secret


class Step(Inputs):
    phase: Literal["remote", "local", "program"]
    snapshot: Snapshot


class RecoveryStep(Inputs):
    operation: str
    plan: RecoveryPlan


class Cleanup(Inputs):
    id: UUID = Field(default_factory=uuid4)
    plugin: Identifier
    digest: str
    steps: tuple[Step, ...]
    recovery: tuple[RecoveryStep, ...]
    secrets: tuple[Resource, ...]
    settings: tuple[Resource, ...]
    generations: tuple[str, ...]
    blockers: tuple[str, ...]
    preserved: tuple[str, ...]

    def details(self) -> str:
        lines = []
        for step in self.steps:
            plan = step.snapshot.mutation.plan
            lines.append(f"{step.phase} · {step.snapshot.mutation.action_id} · {plan.summary}")
            lines.extend(plan.warnings)
            lines.extend(f"{r.effect} {r.kind} · {r.identity}" for r in plan.resources)
        for record in self.recovery:
            lines.extend(f"{status} · {name}" for name, status in record.plan.entries)
        lines.extend("Remove keyring entry · " + r.identity for r in self.secrets)
        lines.extend("Remove settings · " + r.identity for r in self.settings)
        lines.extend("Remove extension · " + g for g in self.generations)
        lines.extend("Preserve · " + p for p in self.preserved)
        lines.extend("Blocked · " + b for b in self.blockers)
        return "\n".join(lines)


async def preview_cleanup(
    manager: PluginManager, store: Settings, plugin: str, values: Values
) -> Cleanup:
    state = await manager.activation(plugin)
    holds = await manager.ledger.holdings(plugin)
    steps, recoveries, settings, preserved = [], [], [], []
    blockers: list[str] = []
    covered: set[tuple[str, str]] = set()
    blockers.extend(
        "Required by installed program " + p for p in await manager.ledger.dependents(plugin)
    )
    if await manager.ledger.pending(plugin):
        blockers.append("Reconcile interrupted operations first")
    for phase in ("remote", "local", "program"):
        for action in state.manifest.actions:
            if action.cleanup_phase != phase:
                continue
            snapshot = await manager.preview(plugin, action.id, {}, {}, saved=values)
            plan = snapshot.mutation.plan
            if plan.outcome is Outcome.NO_CHANGES:
                continue
            if (
                plan.outcome is not Outcome.READY
                or not plan.resources
                or any(
                    r.effect != "remove"
                    or not any(
                        h.kind == r.kind
                        and h.identity == r.identity
                        and h.cleanup
                        and h.ownership == "created"
                        and h.after == r.before
                        for h in holds
                    )
                    for r in plan.resources
                )
            ):
                if any(h.cleanup and h.kind != "file" for h in holds):
                    blockers.append(action.label + " · " + plan.summary)
                continue
            steps.append(Step(phase=phase, snapshot=snapshot))
            covered.update((r.kind, r.identity) for r in plan.resources)
    for operation, receipt in await manager.ledger.recoveries(plugin):
        record = await asyncio.to_thread(RecoveryPlan.inspect, receipt)
        if record.entries and all(("file", name) in covered for name, _ in record.entries):
            continue
        if any(s == "conflict" for _, s in record.entries):
            blockers.append("Review later edits for receipt " + receipt)
        recoveries.append(RecoveryStep(operation=operation, plan=record))
        covered.update(("file", name) for name, _ in record.entries)
    for profile in await asyncio.to_thread(profiles, store, plugin):
        saved = await store.read(plugin, profile)
        if saved.revision.endswith(":absent"):
            continue
        row = next(
            (
                h
                for h in holds
                if h.kind == "plugin_settings"
                and h.identity == profile_path(store, plugin, profile)
            ),
            None,
        )
        if row and row.cleanup and row.after == saved.revision:
            settings.append(row)
            covered.add((row.kind, row.identity))
        else:
            preserved.append(f"Externally created or edited profile {profile}")
    secrets = []
    for row in holds:
        if row.kind == "secret-reference" and row.cleanup and row.ownership == "created":
            if row.after == row.identity and await secret_exists(plugin, UUID(row.identity)):
                secrets.append(row)
                covered.add((row.kind, row.identity))
        if (row.kind, row.identity) in covered:
            continue
        if row.cleanup and row.ownership == "created":
            blockers.append("No verified removal for " + row.kind + " · " + row.identity)
        else:
            preserved.append(row.kind + " · " + row.identity)
    return Cleanup(
        plugin=plugin,
        digest=state.digest,
        steps=tuple(steps),
        recovery=tuple(recoveries),
        secrets=tuple(secrets),
        settings=tuple(settings),
        generations=await asyncio.to_thread(removable, manager.env_root, plugin),
        blockers=tuple(blockers),
        preserved=tuple(preserved),
    )


def profile_path(store: Settings, plugin: str, profile: str) -> str:
    return str(store.directory / "plugins" / plugin / (profile + ".toml"))


async def save_profile(
    manager: PluginManager,
    store: Settings,
    plugin: str,
    profile: str,
    values: Values,
    version: int,
    revision: str,
) -> None:
    operation = uuid4()
    row = Resource(
        kind="plugin_settings",
        identity=profile_path(store, plugin, profile),
        before=revision,
        ownership="created" if revision.endswith(":absent") else "modified",
        cleanup=revision.endswith(":absent"),
    )
    await manager.ledger.prepare(
        operation,
        plugin,
        "save-settings",
        str(version),
        Plan(summary="Save profile", outcome=Outcome.READY, resources=[row]),
    )
    try:
        saved = await store.publish(plugin, profile, values, version, revision)
    except (ValueError, OSError):
        await manager.ledger.finish(
            operation, Result(outcome=Outcome.BLOCKED, message="Profile save stopped")
        )
        raise
    await manager.ledger.finish(
        operation,
        Result(
            outcome=Outcome.COMPLETE,
            message="Profile saved",
            resources=[row.model_copy(update={"after": saved.revision})],
        ),
    )


async def apply_cleanup(
    manager: PluginManager,
    store: Settings,
    plan: Cleanup,
    authorize: Callable[[Action], Awaitable[BecomePassword | None]],
) -> Result:
    if plan.blockers or (await manager.activation(plan.plugin)).digest != plan.digest:
        return Result(
            outcome=Outcome.BLOCKED, message="Resolve the listed conflicts and preview again"
        )

    async def actions(phase: str) -> bool:
        for step in plan.steps:
            if step.phase != phase:
                continue
            snap = step.snapshot
            action = next(
                a
                for a in (await manager.activation(plan.plugin)).manifest.actions
                if a.id == snap.mutation.action_id
            )
            password = await authorize(action)
            current = await manager.preview(
                plan.plugin, action.id, snap.mutation.settings, snap.mutation.inputs
            )
            if current.digest != snap.digest or current.mutation.plan != snap.mutation.plan:
                if password:
                    password.discard()
                return False
            result = await manager.apply(current, password)
            if result.outcome not in (Outcome.COMPLETE, Outcome.CHANGED, Outcome.NO_CHANGES):
                return False
        return True

    await manager.journal.emit(
        Event(
            plugin_id=plan.plugin,
            operation_id=plan.id,
            action_id="full-cleanup",
            phase="prepared",
            message_code="cleanup_prepared",
        )
    )
    try:
        if not await actions("remote") or not await actions("local"):
            raise ValueError("An extension resource still needs attention")
        for recovery in plan.recovery:
            result = await manager.rollback(plan.plugin, recovery.operation, recovery.plan)
            if result.outcome is not Outcome.COMPLETE:
                raise ValueError("A file recovery remains unresolved")
        if not await actions("program"):
            raise ValueError("Program removal requires review")
        for secret in plan.secrets:
            await remove_keyring_entry(manager, plan.plugin, secret)
        for row in plan.settings:
            await forget_profile(store, manager.ledger, plan.plugin, row)
        await asyncio.to_thread(retire, manager.env_root, plan.plugin, plan.generations)
    except (ValueError, OSError, RuntimeError):
        result = Result(
            outcome=Outcome.PARTIAL,
            message="Cleanup stopped. Review resources and preview remaining steps.",
        )
    else:
        result = Result(outcome=Outcome.COMPLETE, message="Verified cleanup completed")
    await manager.journal.emit(
        Event(
            plugin_id=plan.plugin,
            operation_id=plan.id,
            action_id="full-cleanup",
            phase="verified" if result.outcome is Outcome.COMPLETE else "unresolved",
            message_code="cleanup_finished",
            outcome=result.outcome,
        )
    )
    return result


async def save_keyring_entry(manager: PluginManager, plugin: str, value: SecretStr) -> UUID:
    operation, reference = uuid4(), uuid4()
    row = Resource(
        kind="secret-reference",
        identity=str(reference),
        after=str(reference),
        ownership="created",
        cleanup=True,
    )
    await manager.ledger.prepare(
        operation,
        plugin,
        "save-secret",
        "1",
        Plan(summary="Save scoped keyring entry", resources=[row]),
    )
    try:
        await store_secret(plugin, value, reference)
        verified = await secret_exists(plugin, reference)
    except RuntimeError:
        await manager.ledger.finish(
            operation, Result(outcome=Outcome.PARTIAL, message="Inspect the native keyring")
        )
        raise
    result = Result(
        outcome=Outcome.COMPLETE if verified else Outcome.PARTIAL,
        message="Inspected native keyring reference",
        resources=[row] if verified else [],
    )
    await manager.ledger.finish(operation, result)
    if not verified:
        raise ValueError("Keyring did not retain the private reference")
    return reference


async def remove_keyring_entry(manager: PluginManager, plugin: str, row: Resource) -> None:
    operation = uuid4()
    change = row.model_copy(update={"effect": "remove", "before": row.after, "after": None})
    await manager.ledger.prepare(
        operation,
        plugin,
        "remove-secret",
        "1",
        Plan(summary="Remove owned keyring entry", resources=[change]),
    )
    try:
        ref = UUID(row.identity)
        await forget_secret(plugin, ref)
        if await secret_exists(plugin, ref):
            raise ValueError("Native keyring reference is still present")
    except (RuntimeError, ValueError):
        await manager.ledger.finish(
            operation, Result(outcome=Outcome.PARTIAL, message="Keyring cleanup needs review")
        )
        raise
    await manager.ledger.finish(
        operation,
        Result(
            outcome=Outcome.COMPLETE,
            message="Verified removal of keyring reference",
            resources=[change],
        ),
    )
