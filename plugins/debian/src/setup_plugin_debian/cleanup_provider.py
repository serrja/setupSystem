"""System package cleanup with a protected catalog and exact removal closure."""

import os
from typing import ClassVar, Literal

from platformdirs import user_config_path
from pydantic import Field
from setup_system_sdk.blocking import run_sync
from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.permissions import Permission
from setup_system_sdk.enums.resources import ResourceEffect, ResourceOwnership
from setup_system_sdk.operations import Plan, Receipt, Result, StepPlan
from setup_system_sdk.providers import CapabilityRequest
from setup_system_sdk.resources import Resource
from setup_system_sdk.schemas.common import Inputs, Values

from setup_plugin_debian.adapters import cleanup as native
from setup_plugin_debian.adapters.native import run_native_play
from setup_plugin_debian.adapters.platform import current_target


class CleanupInput(Inputs):
    packages: list[str] = Field(min_length=1, max_length=64)


class CleanupRequest(Inputs):
    consumer: Literal["cleanup"]
    action: native.Mode | Literal["inspect"]
    inputs: Values
    evidence: tuple[Resource, ...] | None = None


def cleanup_request(payload: Values) -> CleanupRequest:
    return CleanupRequest.model_validate(payload)


def cleanup_plan(params: CleanupRequest) -> tuple[Plan, native.Confirmation]:
    mode = native.Mode(params.action)
    selected = CleanupInput.model_validate(params.inputs).packages
    rows = native.preview(mode, selected, os.getuid(), user_config_path())
    fingerprint = native.digest(rows)
    plan = Plan(
        outcome=Outcome.READY if rows else Outcome.NO_CHANGES,
        summary=f"Review {len(rows)} package removals",
        warnings=[native.catalog()[row.name].reason for row in rows],
        resources=[
            Resource(
                kind="package",
                identity=row.name,
                ownership=ResourceOwnership.PREEXISTING,
                before=f"{row.architecture}:{row.version}:{fingerprint}",
                effect=ResourceEffect.REMOVE,
            )
            for row in rows
        ],
    )
    return plan, native.Confirmation(
        mode=mode,
        selected=selected,
        caller_uid=os.getuid(),
        config_home=str(user_config_path()),
        expected=fingerprint,
    )


def run_cleanup(params: CleanupRequest, expected: Plan) -> None:
    plan, confirmation = cleanup_plan(params)
    if plan != expected:
        raise ValueError("Package inventory changed")
    if plan.outcome == Outcome.READY:
        run_native_play("debian", "@cleanup", confirmation.model_dump(mode="json"), "sudo")


def readback(params: CleanupRequest, rows: tuple[Resource, ...]) -> Result:
    arch, supported = current_target()
    if not supported:
        return Result(outcome=Outcome.UNKNOWN_OUTCOME, message="Cannot inspect this platform")
    present = {
        row.name
        for row in native.inventory([r.identity for r in rows], native.Mode(params.action), arch)
    }
    observed = [r.model_copy(update={"verified": True}) for r in rows if r.identity not in present]
    return Result(
        outcome=Outcome.COMPLETE
        if not present
        else Outcome.PARTIAL
        if observed
        else Outcome.UNKNOWN_OUTCOME,
        message="Checked the selected package states",
        resources=observed,
    )


class CleanupProvider:
    request_model: ClassVar[type[Inputs]] = CleanupRequest

    async def inspect(self, request: CapabilityRequest, context: PluginContext) -> Values:
        params = cleanup_request(request.payload)
        if params.action == "inspect":
            arch, supported = await run_sync(current_target)
            if supported:
                keys = list(await run_sync(native.catalog))
                present = await run_sync(native.inventory, keys, native.Mode.REMOVE, arch)
                return {
                    "result": Result(
                        outcome=Outcome.READY, message=", ".join(r.name for r in present)
                    ).model_dump(mode="json")
                }
            return {
                "result": Result(
                    outcome=Outcome.BLOCKED, message="Unsupported platform"
                ).model_dump(mode="json")
            }
        if params.evidence is not None:
            return {
                "result": (await run_sync(readback, params, params.evidence)).model_dump(
                    mode="json"
                )
            }
        try:
            plan, _ = await run_sync(cleanup_plan, params)
        except (ValueError, OSError):
            plan = Plan(
                outcome=Outcome.BLOCKED,
                summary="Cleanup catalog or package simulation is unavailable",
            )
        return {"proposal": plan.model_dump(mode="json")}

    async def plan(self, request: CapabilityRequest, context: PluginContext) -> StepPlan:
        params = cleanup_request(request.payload)
        plan, _ = await run_sync(cleanup_plan, params)
        return StepPlan(
            step_key=request.step_key,
            executor=context.executor,
            capability=request.capability,
            contract_version="1",
            handler="package-cleanup",
            preconditions={"proposal": plan.model_dump(mode="json")},
            payload=request.payload,
            resources=tuple(plan.resources),
            permissions=frozenset({Permission.ROOT, Permission.REMOVE_PREEXISTING}),
            cancellation="safe_boundary",
            lock_keys=("system.packages",),
        )

    async def execute(self, step: StepPlan, context: PluginContext) -> Receipt:
        return await context.execute_native()

    async def readback(
        self, step: StepPlan, receipt: Receipt | None, context: PluginContext
    ) -> Result:
        return await run_sync(readback, cleanup_request(step.payload), step.resources)
