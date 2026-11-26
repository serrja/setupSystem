"""Plan catalog selections, guard each native change and read back signed revisions."""

from __future__ import annotations

import os
import re
import subprocess

import httpx
import yaml
from setup_system_sdk.blocking import run_sync
from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.resources import ResourceEffect, ResourceOwnership
from setup_system_sdk.files.native_tool import query
from setup_system_sdk.providers import CapabilityRequest
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan, Resource, Result

from setup_plugin_snap import daemon
from setup_plugin_snap.adapters.native import TOOL, current_target
from setup_plugin_snap.catalog import Selection, catalog
from setup_plugin_snap.enums import SnapAction
from setup_plugin_snap.store import Revision, StoreSpec, latest

SYSTEM_SNAP = "snapd"
SYSTEM_IDENTITY = ("PMrrV4ml8uWuEUDBT8dSGnKUYbevVhc4", "canonical")
FAILURES = (
    OSError,
    ValueError,
    TimeoutError,
    httpx.HTTPError,
    subprocess.SubprocessError,
    yaml.YAMLError,
)


class Approval(Inputs):
    action: SnapAction
    selection: Selection
    plan: Plan


class ApprovedChange(Inputs):
    approval: Approval


def expected_identity(name: str) -> tuple[str, str] | None:
    entry = catalog().get(name)
    if entry:
        return entry.snap_id, entry.publisher_id
    return SYSTEM_IDENTITY if name == SYSTEM_SNAP else None


async def observation(item: daemon.Native) -> Revision:
    identity = expected_identity(item.name)
    if identity and item.id != identity[0]:
        raise ValueError("The installed application has a different Store identity")
    return await daemon.actual(item, identity[1] if identity else None)


async def inventory(local: httpx.AsyncClient) -> tuple[dict[str, daemon.Native], set[str]]:
    rows = await daemon.installed(local, all_revisions=True)
    active_rows = [item for item in rows if item.status == "active"]
    active = {item.name: item for item in active_rows}
    if len(active) != len(active_rows):
        raise ValueError("The active snapd inventory is ambiguous")
    return active, {item.name for item in rows}


async def plan_selection(
    action: SnapAction,
    selection: Selection,
    local: httpx.AsyncClient,
    store: httpx.AsyncClient,
    context: PluginContext | None = None,
) -> Plan:
    arch, supported = await run_sync(current_target)
    entries = catalog()
    selected = selection.applications
    if not supported or any(arch not in entries[name].platforms for name in selected):
        return Plan(
            outcome=Outcome.BLOCKED,
            summary="This catalog selection requires a supported Debian platform.",
        )
    current, present_names = await inventory(local)
    resources: list[Resource] = []
    visited: set[str] = set()
    path: set[str] = set()
    specs: dict[str, StoreSpec] = {}
    warnings: list[str] = []

    async def visit(name: str) -> None:
        if name in visited:
            return
        if name in path or len(path) + len(visited) >= 48:
            raise ValueError("The snap dependency graph is ambiguous or too large")
        path.add(name)
        identity = expected_identity(name)
        if name in present_names and name not in current:
            raise ValueError("Inspect the installed inactive revisions before proceeding")
        observed = await observation(current[name]) if name in current else None
        old = observed.model_dump_json() if observed else None
        desired = old
        effect = ResourceEffect.ENSURE
        owner = ResourceOwnership.PREEXISTING
        if action is SnapAction.REMOVE_APPS:
            if observed:
                effect = ResourceEffect.REMOVE
                desired = None
                warnings.append(entries[name].removal)
        elif action is SnapAction.CHECK_APPS:
            pass
        elif observed and (name not in selected or action is SnapAction.INSTALL_APPS):
            pass
        else:
            if not observed and name in selected:
                if action is SnapAction.UPDATE_APPS:
                    raise ValueError("Install the selected application before updating it")
                if context is not None:
                    facts = await context.inspect_provider(
                        CapabilityRequest(
                            capability="system.packages",
                            step_key="inventory",
                            payload={"action": "install", "consumer": "snap", "inventory": [name]},
                        )
                    )
                    versions = facts.get("installed")
                    if not isinstance(versions, dict) or name not in versions:
                        raise ValueError("Incomplete package inventory")
                    version = versions[name]
                elif os.geteuid() == 0:
                    version = await run_sync(query, TOOL, "installed", name)
                else:
                    raise ValueError("A verified package inventory is required")
                if version:
                    raise ValueError(
                        "Review the existing Debian application before switching to Snap"
                    )
            target, spec = await latest(store, name, arch, identity)
            specs[name] = spec
            requirements = spec.requirements()
            if name != SYSTEM_SNAP:
                requirements.setdefault(SYSTEM_SNAP, set())
            for dep, tags in sorted(requirements.items()):
                await visit(dep)
                if tags:
                    available = (
                        specs[dep].content() if dep in specs else await daemon.content(local, dep)
                    )
                    if not tags <= available:
                        raise ValueError("A required content provider has different slots")
            desired = target.model_dump_json()
            owner = ResourceOwnership.CREATED if not observed else ResourceOwnership.MODIFIED
        path.remove(name)
        visited.add(name)
        resources.append(
            Resource(
                kind="snap",
                identity=name,
                before=old,
                after=desired,
                effect=effect,
                ownership=owner if name in selected else ResourceOwnership.SHARED,
                cleanup=False,
            )
        )

    for name in selected:
        await visit(name)
    labels = ", ".join(entries[name].title for name in selected)
    if action is SnapAction.CHECK_APPS:
        labels = ", ".join(
            f"{entries[name].title} ({current[name].version if name in current else 'absent'})"
            for name in selected
        )
    return Plan(
        summary=f"{labels} · {action.value}",
        resources=resources,
        warnings=warnings,
        outcome=Outcome.READY
        if any(row.before != row.after for row in resources)
        else Outcome.NO_CHANGES,
    )


async def preview(action: SnapAction, selection: Selection, context: PluginContext | None) -> Plan:
    try:
        async with (
            daemon.connection() as local,
            httpx.AsyncClient(timeout=10, trust_env=False) as store,
        ):
            return await plan_selection(action, selection, local, store, context)
    except FAILURES:
        return Plan(
            outcome=Outcome.BLOCKED,
            summary="Check snapd, installed revisions and the signed Store dependencies.",
        )


async def readback(local: httpx.AsyncClient, plan: Plan) -> Result:
    installed_rows, present_names = await inventory(local)
    checked = []
    for resource in plan.resources:
        item = installed_rows.get(resource.identity)
        expected = resource.after
        if item:
            actual = (await observation(item)).model_dump_json()
            matches = actual == expected
        else:
            actual = None
            matches = expected is None and resource.identity not in present_names
        if matches:
            checked.append(resource.model_copy(update={"after": actual, "verified": True}))
    return Result(
        outcome=Outcome.COMPLETE if len(checked) == len(plan.resources) else Outcome.PARTIAL,
        message="Checked the installed snap identities and signed revisions.",
        resources=checked,
    )


async def apply(approval: Approval) -> None:
    if os.geteuid() != 0 or approval.action not in (
        SnapAction.INSTALL_APPS,
        SnapAction.UPDATE_APPS,
        SnapAction.REMOVE_APPS,
    ):
        raise ValueError("A reviewed administrative Snap action is required")
    async with (
        daemon.connection() as local,
        httpx.AsyncClient(timeout=10, trust_env=False) as store,
    ):
        plan = await plan_selection(approval.action, approval.selection, local, store)
        if plan != approval.plan or plan.outcome is not Outcome.READY:
            raise ValueError(
                "The platform, catalog release or installed state changed after confirmation"
            )
        completed: list[Resource] = []
        for row in plan.resources:
            if (
                completed
                and (
                    await readback(local, Plan(summary="Completed revisions", resources=completed))
                ).outcome
                is not Outcome.COMPLETE
            ):
                raise ValueError("A shared revision changed during the operation")
            items, present_names = await inventory(local)
            if row.identity in present_names and row.identity not in items:
                raise ValueError("The snap became inactive during the operation")
            current = await observation(items[row.identity]) if row.identity in items else None
            if (current.model_dump_json() if current else None) != row.before:
                raise ValueError("The installed revision changed during the operation")
            if row.before == row.after:
                completed.append(row)
                continue
            if row.effect is ResourceEffect.REMOVE:
                if current is None:
                    raise ValueError("The selected snap disappeared")
                await daemon.preserve_data(local, current)
                present, _ = await inventory(local)
                if (
                    current.name not in present
                    or await observation(present[current.name]) != current
                ):
                    raise ValueError("The snap changed while saving its data")
                await daemon.checked_tool("remove", current.name, timeout=600)
            else:
                if row.after is None:
                    raise ValueError("The confirmed Store revision is missing")
                desired = Revision.model_validate_json(row.after)
                change = (
                    await daemon.checked_tool(
                        "refresh" if current else "install",
                        "--no-wait",
                        "--channel=latest/stable",
                        f"--revision={desired.revision}",
                        desired.name,
                    )
                ).strip()
                if not re.fullmatch(r"[1-9][0-9]*", change):
                    raise ValueError("Snapd returned an unknown change identity")
                await daemon.checked_tool("watch", change, timeout=600)
            completed.append(row)
        if (await readback(local, plan)).outcome is not Outcome.COMPLETE:
            raise ValueError("Snapd readback differs from the confirmed plan")
