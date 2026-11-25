"""Operate on the bounded regional, login, boot and enterprise-policy recovery contracts."""

import hashlib
import json
import os
import stat
import subprocess
import sys
from enum import StrEnum
from pathlib import Path
from typing import Literal

import typer
from pydantic import JsonValue

from setup_system.domain.actions import RegionAction, SystemAction
from setup_system.domain.contracts import MAX_MESSAGE_BYTES, ContractModel
from setup_system.domain.identity import TargetUser
from setup_system.domain.intentions import BaseOptions, WorkspaceOptions
from setup_system.domain.observations import HappBefore, HappUnit, SystemBefore
from setup_system.domain.system_receipts import RegionalKind
from setup_system.domain.system_requests import HappInspection, StageApply, SystemIntent
from setup_system.infrastructure import firefox_policy, graphical_unlock, happ_facts, regional
from setup_system.infrastructure.identity import resolve_target, user_environment
from setup_system.infrastructure.login import DesktopSystem
from setup_system.infrastructure.native_target import current_target
from setup_system.infrastructure.system_paths import protected
from setup_system.layout import require_installed_resource, resource_root
from setup_system.machine_io import write_model


def desktop(xdg: dict[str, str]) -> tuple[DesktopSystem, TargetUser, dict[str, str]]:
    target = resolve_target(None)
    env = user_environment(target, {k: Path(v) for k, v in xdg.items()})
    return DesktopSystem(resource_root()), target, env


def policy_stamp(
    path: Path = Path("/etc/firefox/policies/policies.json"),
) -> tuple[int, int, int, str] | Literal["root-only"] | None:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1024 * 1024:
            raise ValueError("Unsafe Firefox enterprise policy")
        raw = path.read_bytes()
        return (
            info.st_dev,
            info.st_ino,
            stat.S_IMODE(info.st_mode),
            hashlib.sha256(raw).hexdigest(),
        )
    except FileNotFoundError:
        return None
    except PermissionError:
        return "root-only"


def policy_matches(stamp: tuple[int, int, int, str] | Literal["root-only"] | None) -> bool:
    return bool(
        isinstance(stamp, tuple)
        and stamp[2] == 0o644
        and stamp[3] == hashlib.sha256(firefox_policy.desired_bytes()).hexdigest()
    )


class SystemTask(StrEnum):
    PLAN = "plan"
    HAPP_INSPECT = "happ-inspect"
    HAPP_UNIT = "happ-unit"
    REGION = "region"
    LOGIN = "login"
    FIREFOX = "firefox"
    BOOT = "boot"


class SystemPlan(ContractModel):
    actions: tuple[RegionAction | SystemAction, ...]
    blockers: tuple[str, ...]
    observed: SystemBefore


cli = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


@cli.command()
def run(op: SystemTask) -> None:
    """Inspect a system stage or apply its confirmed operation."""
    if op not in {SystemTask.PLAN, SystemTask.HAPP_INSPECT}:
        if os.geteuid() != 0 or not current_target()[1]:
            raise ValueError("System changes require authenticated Debian 13 system execution")
        require_installed_resource(Path(__file__), "ansible/files/system_contract.py")
    wire = sys.stdin.read(MAX_MESSAGE_BYTES + 1)
    if len(wire.encode()) > MAX_MESSAGE_BYTES:
        raise ValueError("System intent exceeds the supported size")
    if op == SystemTask.HAPP_INSPECT:
        inspection = HappInspection.model_validate_json(wire)
        if "happ" not in inspection.settings.apps:
            raise ValueError("Choose Happ before inspecting its service")
        unit = happ_facts.unit_info()
        installed = happ_facts.installed_version()
        if inspection.exact or installed is None or installed.split("-")[0] != inspection.version:
            happ_facts.require_idle(installed, inspection.version, unit)
        write_model(HappBefore(unit=HappUnit.model_validate(unit), installed=installed))
        return
    if op == SystemTask.PLAN:
        intent = SystemIntent.model_validate_json(wire)
        settings = intent.settings
        actions: list[RegionAction | SystemAction] = []
        reasons: list[str] = []
        observations: dict[str, JsonValue] = {}
        if intent.section == "base" and isinstance(settings, BaseOptions):
            kinds: tuple[RegionalKind, ...] = ("timezone", "locale", "keyboard")
            for kind in kinds:
                if (selected := getattr(settings, kind)) is not None:
                    desired = regional.validate(kind, selected)
                    actual = regional.actual(kind)
                    observations[kind] = actual
                    if not regional.equivalent(kind, actual, desired):
                        actions.append(
                            RegionAction(
                                kind="region",
                                field=kind,
                                value=desired,
                                label=f"Set {kind} to {desired}",
                            )
                        )
            if settings.graphical_login and intent.xdg is not None:
                manager, account, env = desktop(intent.xdg.model_dump())
                plan = manager.plan(account, env)
                observations["login"] = plan.consent.model_dump(mode="json")
                if (
                    any(
                        item.observed != hashlib.sha256(item.content).hexdigest()
                        or item.observed_mode != item.mode
                        for item in plan.files
                    )
                    or not plan.grub_managed
                    or plan.previous_target != "graphical.target"
                    or not plan.consent.manager_enabled
                ):
                    actions.append(
                        SystemAction(
                            kind="graphical_login",
                            label="Enable greetd with a recoverable GRUB menu",
                        )
                    )
            if settings.plymouth:
                unlock = graphical_unlock.inspect_unlock()
                observations["unlock"] = unlock.model_dump(mode="json", exclude={"description"})
                reasons.extend(unlock.blockers)
                if not unlock.blockers:
                    observations["unlock_current"] = graphical_unlock.is_current(resource_root())
                    if not observations["unlock_current"]:
                        actions.append(SystemAction(kind="plymouth", label=unlock.description))
        elif isinstance(settings, WorkspaceOptions) and "firefox" in settings.apps:
            stamp = policy_stamp()
            observations["firefox"] = list(stamp) if isinstance(stamp, tuple) else stamp
            if not policy_matches(stamp):
                actions.append(
                    SystemAction(
                        kind="firefox_policy", label="Apply Firefox enterprise privacy policy"
                    )
                )
        write_model(
            SystemPlan(
                actions=tuple(actions),
                blockers=tuple(reasons),
                observed=SystemBefore.model_validate(observations),
            )
        )
        return
    stage = StageApply.model_validate_json(wire)
    stage.require_operation(op)
    before = stage.observed
    if op == SystemTask.HAPP_UNIT:
        info = happ_facts.unit_info()
        if (
            not any(
                path.is_file()
                for path in (
                    Path("/etc/systemd/system/happd.service"),
                    Path("/usr/lib/systemd/system/happd.service"),
                )
            )
            or happ_facts.installed_version() is None
        ):
            raise ValueError("The verified Debian package did not provide its service")
        print(json.dumps(info))
    elif op == SystemTask.REGION:
        for item in stage.actions:
            if isinstance(item, RegionAction):
                original = getattr(before, item.field)
                if (
                    original is None
                    and item.field == "keyboard"
                    and regional.actual(item.field) == "us"
                ):
                    original = "us"
                regional.apply(item.field, item.value, original)
    elif op == SystemTask.LOGIN and stage.xdg is not None and before.login is not None:
        manager, account, env = desktop(stage.xdg.model_dump())
        changed = manager.apply(manager.revalidate(before.login, account, env))
        print(json.dumps({"changed": changed}))
    elif op == SystemTask.FIREFOX:
        if before.firefox != "root-only" and before.firefox != policy_stamp():
            raise ValueError("Firefox policy changed after confirmation")
        firefox_policy.apply(firefox_policy.inspect())
        if not policy_matches(policy_stamp()):
            raise RuntimeError("Firefox enterprise policy readback failed")
    elif op == SystemTask.BOOT and before.unlock is not None:
        accepted_unlock = before.unlock

        def install(packages: tuple[str, ...]) -> None:
            apt_stage = Path(stage.apt_work)
            if apt_stage.parent != Path("/run/setup-system") or not apt_stage.name.startswith(
                "setup-system-apt-"
            ):
                raise ValueError("Expected a private root APT stage")
            protected(apt_stage)
            subprocess.run(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    str(resource_root() / "ansible/files/apt_apply.py"),
                    "apply",
                    str(apt_stage),
                ],
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        changed = graphical_unlock.apply_unlock(
            accepted_unlock.identity,
            root=resource_root(),
            packages=accepted_unlock.packages,
            install=install,
        )
        print(json.dumps({"changed": changed}))
    else:
        raise ValueError("Unknown or unselected system operation")


if __name__ == "__main__":
    cli()
