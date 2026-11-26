"""Prepare a recoverable graphical initramfs prompt for an existing LUKS root."""

from __future__ import annotations

import asyncio
import hashlib
import stat
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Literal

from setup_system.execution.inspection import observations
from setup_system.execution.native_target import current_target
from setup_system.execution.recipes.apt import Approval, package_plan, verify_packages
from setup_system.execution.recipes.native_play import run_native_play
from setup_system.execution.system_paths import protected
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.resources import ResourceOwnership
from setup_system_sdk.plugin import Backend, PluginSettings
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan, Resource, Result

from . import boot

FOLDER = Path(__file__).parent


class Confirmation(Inputs):
    action: Literal[LifecycleAction.INSTALL, LifecycleAction.UPDATE, LifecycleAction.CONFIGURE]
    plan: Plan


def prepare(action: str) -> Plan:
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
    packages = (
        package_plan(FOLDER)
        if action in (LifecycleAction.INSTALL, LifecycleAction.UPDATE)
        else None
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
    if packages:
        resources.extend(packages.resources)
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


class Plugin(Backend):
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = dict.fromkeys(
        (
            LifecycleAction.INSTALL,
            LifecycleAction.UPDATE,
            LifecycleAction.CONFIGURE,
            LifecycleAction.CHECK,
        ),
        Inputs,
    )

    async def inspect(self, settings: PluginSettings) -> Result:
        plan, facts = await asyncio.gather(
            asyncio.to_thread(prepare, "check"), asyncio.to_thread(observations, Path.home())
        )
        return Result(outcome=plan.outcome, message="\n".join((plan.summary, *facts)))

    async def plan(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        return await asyncio.to_thread(prepare, action_id)

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        return await asyncio.to_thread(observe_boot, plan)


def observe_boot(plan: Plan) -> Result:
    facts = boot.inspect_unlock()
    if facts.blockers or not facts.identity or not boot.is_current(None):
        return Result(outcome=Outcome.PARTIAL, message="Boot file readback is incomplete.")
    rows = []
    packages = [row for row in plan.resources if row.kind == "package"]
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
    if packages:
        inspected = verify_packages(Plan(summary=plan.summary, resources=packages))
        rows.extend(row for row in inspected.resources if row.verified)
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


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    if action_id not in (
        LifecycleAction.INSTALL,
        LifecycleAction.UPDATE,
        LifecycleAction.CONFIGURE,
    ):
        raise ValueError("Unexpected Plymouth operation.")
    confirmation = Confirmation.model_validate({"action": action_id, "plan": plan})
    run_native_play("plymouth", "configure.yml", confirmation.model_dump(mode="json"), "sudo")


def apply_boot(change: Confirmation) -> None:
    from setup_system.execution.recipes.apt import privileged

    if prepare(change.action) != change.plan or change.plan.outcome != Outcome.READY:
        raise ValueError("Boot topology, package versions or recovery destinations changed.")
    observed = boot.inspect_unlock()
    packages = (
        package_plan(FOLDER)
        if change.action in {LifecycleAction.INSTALL, LifecycleAction.UPDATE}
        else None
    )
    if observed.identity != change.plan.resources[0].identity or (
        packages and packages.resources != [r for r in change.plan.resources if r.kind == "package"]
    ):
        raise ValueError("Boot identity or package closure changed after confirmation.")
    if packages and packages.outcome == Outcome.READY:
        if change.action == LifecycleAction.CONFIGURE:
            raise ValueError("Install requires its own confirmation.")
        approval = Approval(plugin_id="plymouth", action_id=change.action, plan=packages)
        boot.apply_unlock(
            observed.identity, packages=observed.packages, install=lambda _: privileged(approval)
        )
    else:
        boot.apply_unlock(observed.identity)
