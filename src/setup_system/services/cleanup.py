"""Compose a reviewed cleanup from package actions, receipts and exact user-owned resources."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, SecretStr

from setup_system.execution.authorization import BecomePassword
from setup_system.execution.native_runner import Snapshot
from setup_system.execution.receipts import FileState
from setup_system.execution.recipes.secrets import forget_secret, secret_exists, store_secret
from setup_system.services.extension_lifecycle import removable, retire
from setup_system.services.operations import Operations
from setup_system.services.rollback import RecoveryPlan
from setup_system.services.settings import SettingsStore
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.resources import RecoveryStep, ResourceEffect, ResourceOwnership
from setup_system_sdk.schemas.common import Identifier, Inputs, Values
from setup_system_sdk.schemas.manifest import Action
from setup_system_sdk.schemas.operation import Resource, Result


class Step(Inputs):
    phase: Literal["remote", "local", "program"]
    snapshot: Snapshot


class RecoveryStep(Inputs):
    operation: UUID
    plan: RecoveryPlan


class Cleanup(Inputs):
    id: UUID = Field(default_factory=uuid4)
    plugin: Identifier
    digest: str
    steps: tuple[Step, ...]
    recovery: tuple[RecoveryStep, ...]
    secrets: tuple[Resource, ...]
    holdings: tuple[Resource, ...]
    generations: tuple[str, ...]
    blockers: tuple[str, ...]
    preserved: tuple[str, ...]

    def details(self) -> str:
        lines = []
        for step in self.steps:
            plan = step.snapshot.mutation.plan
            lines.append(f"{step.phase} · {step.snapshot.mutation.action_id} · {plan.summary}")
            lines.extend(plan.warnings)
            lines.extend(r.description() for r in plan.resources)
        for record in self.recovery:
            lines.extend(entry.description() for entry in record.plan.entries)
        lines.extend("Remove keyring entry · " + r.identity for r in self.secrets)
        lines.extend("Remove extension · " + g for g in self.generations)
        lines.extend("Preserve · " + p for p in self.preserved)
        lines.extend("Blocked · " + b for b in self.blockers)
        return "\n".join(lines)


async def preview_cleanup(
    manager: Operations, store: SettingsStore, plugin: str, values: Values
) -> Cleanup:
    state = await manager.activation(plugin)
    holds = await manager.records.holdings(plugin)
    steps, recoveries, preserved = [], [], []
    blockers: list[str] = []
    covered: set[tuple[str, str]] = set()
    blockers.extend(
        "Required by installed program " + p for p in await manager.records.dependents(plugin)
    )
    if await manager.records.pending(plugin):
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
                    r.effect is not ResourceEffect.REMOVE
                    or not any(
                        h.kind == r.kind
                        and h.identity == r.identity
                        and h.cleanup
                        and h.ownership is ResourceOwnership.CREATED
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
    native_removals = covered.copy()
    projected: dict[str, FileState | None] = {}
    for operation, receipt in await manager.records.recoveries(plugin):
        record = await asyncio.to_thread(RecoveryPlan.inspect, receipt)
        if record.entries and all(
            ("file", str(entry.path)) in native_removals for entry in record.entries
        ):
            continue
        record = await asyncio.to_thread(RecoveryPlan.projected, receipt, projected)
        if any(e.status is RecoveryStep.CONFLICT for e in record.entries):
            blockers.append("Review later edits for receipt " + receipt)
        recoveries.append(RecoveryStep(operation=operation, plan=record))
        covered.update(("file", str(entry.path)) for entry in record.entries)
    saved = await store.read(plugin)
    if not saved.revision.endswith(":absent"):
        preserved.append("Saved extension settings")
    secrets = []
    for row in holds:
        if (
            row.kind == "secret-reference"
            and row.cleanup
            and row.ownership is ResourceOwnership.CREATED
        ):
            if row.after == row.identity and await secret_exists(plugin, UUID(row.identity)):
                secrets.append(row)
                covered.add((row.kind, row.identity))
        if (row.kind, row.identity) in covered:
            continue
        if row.cleanup and row.ownership is ResourceOwnership.CREATED:
            blockers.append("No verified removal for " + row.kind + " · " + row.identity)
        else:
            preserved.append(row.kind + " · " + row.identity)
    return Cleanup(
        plugin=plugin,
        digest=state.digest,
        holdings=tuple(holds),
        steps=tuple(steps),
        recovery=tuple(recoveries),
        secrets=tuple(secrets),
        generations=await asyncio.to_thread(removable, manager.env_root, plugin),
        blockers=tuple(blockers),
        preserved=tuple(preserved),
    )


async def apply_cleanup(
    manager: Operations,
    store: SettingsStore,
    plan: Cleanup,
    authorize: Callable[[Action], Awaitable[BecomePassword | None]],
) -> Result:
    if tuple(await manager.records.holdings(plan.plugin)) != plan.holdings:
        return Result(outcome=Outcome.BLOCKED, message="Ownership changed. Preview cleanup again.")
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
        async with manager.records.claim_extension(plan.plugin):
            if (await manager.activation(plan.plugin)).digest != plan.digest:
                raise ValueError("The extension release changed during cleanup")
            if await manager.records.pending(plan.plugin) or any(
                r.cleanup and r.ownership is ResourceOwnership.CREATED
                for r in await manager.records.holdings(plan.plugin)
            ):
                raise ValueError("Resource ownership still requires the installed worker")
            await asyncio.to_thread(retire, manager.env_root, plan.plugin, plan.generations)
            await manager.extensions.remove(plan.plugin)
    except (ValueError, OSError, RuntimeError):
        result = Result(
            outcome=Outcome.PARTIAL,
            message="Cleanup stopped. Review resources and preview remaining steps.",
        )
    else:
        result = Result(outcome=Outcome.COMPLETE, message="Verified cleanup completed")
    return result


async def save_keyring_entry(manager: Operations, plugin: str, value: SecretStr) -> UUID:
    async with manager.records.claim_extension(plugin):
        release = await manager.activation(plugin)
        operation, reference = uuid4(), uuid4()
        if await secret_exists(plugin, reference):
            raise ValueError("Keyring reference collision")
        row = await manager.records.reserve_secret(
            plugin, release.digest, operation, reference, remove=False
        )
    try:
        await store_secret(plugin, value, reference)
        if not await secret_exists(plugin, reference):
            raise ValueError("The native keyring did not retain the new reference")
    except (OSError, ValueError, RuntimeError):
        await manager.records.finish(
            operation,
            Result(
                outcome=Outcome.UNKNOWN_OUTCOME,
                message="Inspect the native keyring before retrying.",
            ),
        )
        raise
    confirmed = row.model_copy(update={"verified": True})
    await manager.records.finish(
        operation,
        Result(
            outcome=Outcome.COMPLETE,
            message="Verified native keyring reference",
            resources=[confirmed],
        ),
    )
    return reference


async def remove_keyring_entry(manager: Operations, plugin: str, row: Resource) -> None:
    async with manager.records.claim_extension(plugin):
        release = await manager.activation(plugin)
        operation, reference = uuid4(), UUID(row.identity)
        change = await manager.records.reserve_secret(
            plugin, release.digest, operation, reference, remove=True
        )
    try:
        await forget_secret(plugin, reference)
        if await secret_exists(plugin, reference):
            raise ValueError("The native keyring still contains the reference")
    except (ValueError, OSError, RuntimeError):
        await manager.records.finish(
            operation,
            Result(
                outcome=Outcome.UNKNOWN_OUTCOME,
                message="Inspect the native keyring before retrying.",
            ),
        )
        raise
    confirmed = change.model_copy(update={"verified": True})
    await manager.records.finish(
        operation,
        Result(
            outcome=Outcome.COMPLETE,
            message="Verified removal of keyring reference",
            resources=[confirmed],
        ),
    )
