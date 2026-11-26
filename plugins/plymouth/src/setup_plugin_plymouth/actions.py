"""Prepare a recoverable graphical initramfs prompt for an existing LUKS root."""

from __future__ import annotations

import asyncio
import hashlib
import stat
from importlib.resources import files as package_files
from pathlib import Path
from uuid import UUID

from setup_system_sdk.blocking import run_sync
from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.permissions import Permission
from setup_system_sdk.enums.resources import ResourceOwnership
from setup_system_sdk.files.system_files import write_system
from setup_system_sdk.files.system_paths import protected
from setup_system_sdk.operations import Receipt, Snapshot, StepPlan
from setup_system_sdk.plugin import Backend, PluginSettings
from setup_system_sdk.providers import CapabilityRequest
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan, Resource, Result

from setup_plugin_plymouth.adapters.native import current_target, observations, run_native_play
from setup_plugin_plymouth.models import Confirmation

from . import boot
from .recovery import BootRecovery

FOLDER = Path(str(package_files("setup_plugin_plymouth").joinpath("resources")))


def prepare(action: str, packages: Plan | None = None) -> Plan:
    if action not in {
        LifecycleAction.INSTALL,
        LifecycleAction.UPDATE,
        LifecycleAction.CONFIGURE,
        LifecycleAction.CHECK,
    }:
        raise ValueError("Unsupported Plymouth action.")
    if not current_target()[1]:
        return Plan(summary="Plymouth requires Debian 13.", outcome=Outcome.BLOCKED)
    facts = boot.inspect_unlock()
    if facts.blockers or not facts.identity:
        return Plan(
            summary=" ".join(facts.blockers) or "Cannot verify boot topology.",
            outcome=Outcome.BLOCKED,
        )
    if "console-setup" in facts.packages:
        return Plan(
            summary="Install console keyboard tools on the Regional settings page first.",
            outcome=Outcome.BLOCKED,
        )
    ready = boot.is_current(None)
    if action == LifecycleAction.CHECK:
        return Plan(
            summary="Boot files inspected. Confirm password and fallback after a test boot.",
            outcome=Outcome.NO_CHANGES if ready else Outcome.NEEDS_USER_ACTION,
        )
    if facts.packages and action == LifecycleAction.CONFIGURE:
        return Plan(
            summary="Select Install to include backup before Debian's package triggers.",
            outcome=Outcome.BLOCKED,
        )
    if packages and packages.outcome not in (Outcome.NO_CHANGES, Outcome.READY):
        return packages
    files = boot._desired(None)
    current = boot.BOOT / ("initrd.img-" + boot._kernel())
    images = (
        sorted(boot.BOOT.glob("initrd.img-*"))
        if packages and packages.outcome == Outcome.READY
        else [current]
    )
    resources = [
        Resource(kind="boot", identity=facts.identity, ownership=ResourceOwnership.PREEXISTING),
        *(
            Resource(
                kind="file",
                identity=str(path),
                before=boot.digest(path),
                after=hashlib.sha256(content).hexdigest(),
                ownership=ResourceOwnership.MODIFIED
                if path.exists()
                else ResourceOwnership.CREATED,
            )
            for path, content in files.items()
        ),
        *(
            Resource(
                kind="boot-file",
                identity=str(path),
                before=facts.identity,
                ownership=ResourceOwnership.MODIFIED,
            )
            for path in (*images, boot.BOOT / "grub/grub.cfg", boot._verification_path())
        ),
    ]
    return Plan(
        summary="Back up selected boot files, rebuild initramfs and verify the GRUB fallback.",
        warnings=[
            "The protected action rechecks crypttab for existing interactive password entries.",
            "Package rollback is limited. Test graphical and text unlock on the authorized target.",
        ],
        resources=resources,
        outcome=Outcome.NO_CHANGES
        if ready and (not packages or packages.outcome == Outcome.NO_CHANGES)
        else Outcome.READY,
    )


class PluginActions(Backend):
    package_capability = "system.packages"

    async def inspect(
        self, settings: PluginSettings, context: PluginContext | None = None
    ) -> Result:
        plan, facts = await asyncio.gather(
            run_sync(prepare, "check"), run_sync(observations, Path.home())
        )
        return Result(outcome=plan.outcome, message="\n".join((plan.summary, *facts)))

    async def plan(
        self,
        action_id: str,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Plan:
        if context is None or action_id == LifecycleAction.CHECK:
            return await run_sync(prepare, action_id)
        request = CapabilityRequest(
            capability=self.package_capability,
            step_key="packages",
            payload={"action": action_id, "consumer": "plymouth", "inputs": {}},
        )
        packages = None
        if action_id in {LifecycleAction.INSTALL, LifecycleAction.UPDATE}:
            packages = Plan.model_validate((await context.inspect_provider(request))["proposal"])
        config = await run_sync(prepare, action_id, packages)
        if config.outcome not in {Outcome.READY, Outcome.NO_CHANGES}:
            return config
        steps = []

        def local(key: str, proposal: Plan) -> StepPlan:
            return StepPlan(
                step_key=key,
                executor=context.executor,
                handler=action_id,
                contract_version="1",
                permissions=frozenset({Permission.ROOT}),
                lock_keys=("boot.config",),
                cancellation="safe_boundary",
                preconditions={"proposal": proposal.model_dump(mode="json")},
                resources=tuple(proposal.resources),
            )

        if packages and packages.outcome == Outcome.READY:
            resource = Resource(
                kind="boot-backup",
                identity=str(context.operation_id),
                after=config.resources[0].identity,
                ownership=ResourceOwnership.CREATED,
            )
            backup = Plan(
                summary="Preserve boot files before package triggers", resources=[resource]
            )
            steps.append(local("backup", backup))
            package_step = await context.plan_provider(request)
            if tuple(packages.resources) != package_step.resources:
                raise ValueError("Package closure changed")
            steps.append(package_step)
        steps.append(local("config", config))
        return Plan(
            summary=config.summary,
            warnings=config.warnings,
            outcome=config.outcome,
            resources=[r for step in steps for r in step.resources],
            steps=tuple(steps),
        )

    async def readback(
        self, step: StepPlan, receipt: Receipt | None, context: PluginContext
    ) -> Result:
        plan = Plan.model_validate(step.preconditions["proposal"])
        if step.step_key == "backup":
            (row,) = step.resources
            stamp = backup_stamp(context.operation_id)
            protected(stamp, directory=False)
            ok = stamp.exists() and stamp.read_text() == row.after
            return Result.observed(
                outcome=Outcome.COMPLETE if ok else Outcome.UNKNOWN_OUTCOME,
                message="Checked protected boot backup evidence",
                resources=list(step.resources) if ok else [],
            )
        return await run_sync(observe_boot, plan)

    async def verify(
        self,
        plan: Plan,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Result:
        if not context or not plan.steps:
            return await run_sync(observe_boot, plan)
        observed = []
        for step in plan.steps:
            if step.capability:
                facts = await context.inspect_provider(
                    CapabilityRequest(
                        capability=step.capability,
                        step_key=step.step_key,
                        payload={
                            **step.payload,
                            "evidence": [r.model_dump(mode="json") for r in step.resources],
                        },
                    )
                )
                result = Result.model_validate(facts["result"])
            else:
                result = await self.readback(step, None, context)
            if result.outcome not in {
                Outcome.COMPLETE,
                Outcome.CHANGED,
                Outcome.NEEDS_REBOOT,
                Outcome.NO_CHANGES,
            }:
                return result
            observed.extend(result.resources)
        return Result(
            outcome=Outcome.NEEDS_REBOOT,
            message="Verify both unlock paths on an authorized test boot.",
            resources=observed,
        )


def observe_boot(plan: Plan) -> Result:
    facts = boot.inspect_unlock()
    if facts.blockers or not facts.identity or not boot.is_current(None):
        return Result(outcome=Outcome.PARTIAL, message="Boot file readback is incomplete.")
    rows = []
    for row in plan.resources:
        path = Path(row.identity)
        actual: str | None
        if row.kind == "package":
            continue
        if row.kind == "boot":
            actual = facts.identity
        elif row.kind == "file":
            actual = boot.digest(path)
            if actual is None or actual != row.after:
                break
        elif row.kind == "boot-file":
            protected(path, directory=False)
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size == 0 or info.st_uid != 0:
                break
            # Restricted initramfs bytes remain private; snapshotted inode facts are readable.
            actual = f"stat:{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}"
        else:
            break
        rows.append(row.model_copy(update={"after": actual, "verified": True}))
    if len(rows) != len(plan.resources):
        return Result(
            outcome=Outcome.PARTIAL,
            message="Some boot resources require inspection.",
            resources=rows,
        )
    return Result(
        outcome=Outcome.NEEDS_REBOOT,
        message="Verify graphical and text unlock on an authorized test boot.",
        resources=rows,
    )


def backup_stamp(operation: UUID) -> Path:
    return boot.STATE.parent / "verified-backups" / str(operation)


def apply_native_step(snapshot: Snapshot, step: StepPlan) -> None:
    confirmation = Confirmation.model_validate(
        {
            "action": snapshot.action_id,
            "plan": step.preconditions["proposal"],
            "step_key": step.step_key,
            "operation": snapshot.operation_id,
        }
    )
    run_native_play("plymouth", "configure.yml", confirmation.model_dump(mode="json"), "sudo")


def apply_boot(change: Confirmation) -> None:
    import os

    if os.geteuid() != 0 or not current_target()[1]:
        raise ValueError("Unsupported native execution context")
    observed = boot.inspect_unlock()
    if observed.blockers or not observed.identity:
        raise ValueError("Boot facts changed")
    if change.step_key == "backup":
        (row,) = change.plan.resources
        if (row.kind, row.identity, row.after) != (
            "boot-backup",
            str(change.operation),
            observed.identity,
        ):
            raise ValueError("Boot backup identity changed")
        destination = boot.STATE / ("before-packages-" + str(change.operation))
        protected(destination)
        destination.mkdir(mode=0o700, parents=True, exist_ok=False)
        paths = (
            *sorted(boot.BOOT.glob("initrd.img-*")),
            boot.BOOT / "grub/grub.cfg",
            *boot._desired(None),
            boot._verification_path(),
        )
        for path in paths:
            protected(path, directory=False)
        BootRecovery(destination, boot._kernel(), paths)
        if boot.inspect_unlock().identity != observed.identity:
            raise ValueError("Boot state changed during backup")
        write_system(
            backup_stamp(change.operation),
            observed.identity.encode(),
            mode=0o644,
            parent_mode=0o755,
        )
        return
    current = prepare(change.action)
    if (
        current != change.plan
        or observed.packages
        or observed.identity != change.plan.resources[0].identity
    ):
        raise ValueError("Boot prerequisites or reviewed files changed")
    if current.outcome == Outcome.READY:
        boot.apply_unlock(observed.identity)
