"""Prepare a recoverable graphical initramfs prompt for an existing LUKS root."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import ClassVar, Literal

from setup_system.infrastructure.inspection import observations
from setup_system.infrastructure.native_target import current_target
from setup_system_sdk import Backend, Inputs, Outcome, Plan, PluginSettings, Resource, Result
from setup_system_sdk.apt import Approval, package_plan
from setup_system_sdk.native_play import run_native_play

from . import boot

FOLDER = Path(__file__).parent


class Confirmation(Inputs):
    action: Literal["install", "update", "configure"]
    plan: Plan


def prepare(action: str) -> Plan:
    if action not in {"install", "update", "configure", "check"}:
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
    if action == "check":
        return Plan(
            summary="Boot files inspected. Confirm password and fallback after a test boot.",
            outcome=Outcome.NO_CHANGES if ready else Outcome.NEEDS_USER_ACTION,
        )
    if facts.packages and action == "configure":
        return Plan(
            summary="Select Install to include backup before Debian's package triggers.",
            outcome=Outcome.BLOCKED,
        )
    packages = package_plan(FOLDER) if action in ("install", "update") else None
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
        Resource(kind="boot", identity=facts.identity, ownership="preexisting"),
        *(
            Resource(
                kind="file",
                identity=str(path),
                before=boot.digest(path),
                after=hashlib.sha256(content).hexdigest(),
                ownership="modified" if path.exists() else "created",
            )
            for path, content in files.items()
        ),
        *(
            Resource(
                kind="boot-file", identity=str(path), before=facts.identity, ownership="modified"
            )
            for path in (*images, boot.BOOT / "grub/grub.cfg", boot._verification_path())
        ),
    ]
    if packages:
        resources.extend(packages.resources)
    return Plan(
        summary="Back up selected boot files, rebuild initramfs and verify the GRUB fallback.",
        warnings=[
            "Package rollback is limited. Test graphical and text unlock on the authorized target."
        ],
        resources=resources,
        outcome=Outcome.NO_CHANGES
        if ready and (not packages or packages.outcome == Outcome.NO_CHANGES)
        else Outcome.READY,
    )


class Plugin(Backend):
    inputs: ClassVar[dict[str, type[Inputs]]] = dict.fromkeys(
        ("install", "update", "configure", "check"), Inputs
    )

    async def inspect(self, settings: PluginSettings) -> Result:
        plan, facts = await asyncio.gather(
            asyncio.to_thread(prepare, "check"), asyncio.to_thread(observations, Path.home())
        )
        return Result(outcome=plan.outcome, message="\n".join((plan.summary, *facts)))

    async def plan(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        return await asyncio.to_thread(prepare, action_id)

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        if not await asyncio.to_thread(boot.is_current, None):
            return Result(outcome=Outcome.PARTIAL, message="Boot file readback is incomplete.")
        return Result(
            outcome=Outcome.NEEDS_REBOOT,
            message="Boot files passed readback. Verify graphical and text unlock on a test boot.",
            resources=plan.resources,
        )


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    if action_id not in ("install", "update", "configure"):
        raise ValueError("Unexpected Plymouth operation.")
    confirmation = Confirmation.model_validate({"action": action_id, "plan": plan})
    run_native_play("plymouth", "configure.yml", confirmation.model_dump(mode="json"), "sudo")


def apply_boot(change: Confirmation) -> None:
    from setup_system_sdk.apt import privileged

    if prepare(change.action) != change.plan or change.plan.outcome != Outcome.READY:
        raise ValueError("Boot topology, package versions or recovery destinations changed.")
    observed = boot.inspect_unlock()
    packages = package_plan(FOLDER) if change.action in {"install", "update"} else None
    if observed.identity != change.plan.resources[0].identity or (
        packages and packages.resources != [r for r in change.plan.resources if r.kind == "package"]
    ):
        raise ValueError("Boot identity or package closure changed after confirmation.")
    if packages and packages.outcome == Outcome.READY:
        if change.action == "configure":
            raise ValueError("Install requires its own confirmation.")
        approval = Approval(plugin_id="plymouth", action_id=change.action, plan=packages)
        boot.apply_unlock(
            observed.identity, packages=observed.packages, install=lambda _: privileged(approval)
        )
    else:
        boot.apply_unlock(observed.identity)
