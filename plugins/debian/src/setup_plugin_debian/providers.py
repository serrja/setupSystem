"""Plan package transactions against installed catalogs and verify exact versions."""

from pathlib import Path
from typing import ClassVar

import anyio
from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.lifecycle import LifecycleAction, PackageAction
from setup_system_sdk.enums.operation import CancellationPolicy, Outcome
from setup_system_sdk.enums.permissions import Permission
from setup_system_sdk.operations import Plan, Receipt, Result, StepPlan
from setup_system_sdk.providers import CapabilityRequest
from setup_system_sdk.schemas.common import Inputs, Values

from setup_plugin_debian.adapters import apt
from setup_plugin_debian.adapters.layout import INSTALLED, require_installed_resource
from setup_plugin_debian.adapters.platform import current_target
from setup_plugin_debian.models import PackageRequest


def catalog(request: PackageRequest) -> Path:
    relative = apt.catalog_relative(request.consumer)
    path = INSTALLED / relative
    require_installed_resource(path, relative)
    return path


def proposal(request: PackageRequest) -> Plan:
    if not current_target()[1]:
        return Plan(
            summary="This release requires Debian 13 and a supported architecture.",
            outcome=Outcome.BLOCKED,
        )
    source = catalog(request)
    if request.action in apt.SERVICE_TRANSITIONS:
        return apt.service_plan(
            request.action, apt.Packages.model_validate_json(source.read_bytes())
        )
    if request.action in (LifecycleAction.INSTALL, LifecycleAction.UPDATE):
        return apt.package_plan(source.parent)
    if request.action is PackageAction.REMOVE_RETIRED:
        return apt.uninstall_plan(
            source.parent, apt.RetiredPackage.model_validate(request.inputs).name
        )
    return apt.uninstall_plan(source.parent)


def observe(request: PackageRequest, plan: Plan) -> Result:
    if request.action in apt.SERVICE_TRANSITIONS:
        policy = apt.Packages.model_validate_json(catalog(request).read_bytes())
        if not policy.service or len(plan.resources) != 1:
            raise ValueError("Missing reviewed system service")
        (row,) = plan.resources
        valid = (
            row.identity == "system/" + policy.service
            and apt.unit_info(policy.service).get("ActiveState") == row.after
        )
        return Result.observed(
            outcome=Outcome.COMPLETE if valid else Outcome.UNKNOWN_OUTCOME,
            message="Checked the reviewed system service",
            resources=plan.resources if valid else [],
        )
    if not plan.resources and plan.outcome is Outcome.NO_CHANGES:
        return Result(outcome=Outcome.NO_CHANGES, message=plan.summary)
    checked = apt.verify_packages(plan)
    if checked.outcome is Outcome.PARTIAL:
        verified = [
            row.model_copy(update={"verified": True})
            for row in plan.resources
            if apt.verify_packages(Plan(summary=plan.summary, resources=[row])).outcome
            is Outcome.COMPLETE
        ]
        return Result(
            outcome=Outcome.PARTIAL if verified else Outcome.UNKNOWN_OUTCOME,
            message="Checked each reviewed package version",
            resources=verified,
        )
    return checked


class PackageProvider:
    request_model: ClassVar[type[Inputs]] = PackageRequest

    async def inspect(self, request: CapabilityRequest, context: PluginContext) -> Values:
        params = PackageRequest.model_validate(request.payload)
        if params.inventory:
            return {
                "installed": await anyio.to_thread.run_sync(
                    lambda: {name: apt.installed(name) for name in params.inventory}
                )
            }
        if params.evidence is not None:
            result = await anyio.to_thread.run_sync(
                observe, params, Plan(summary="Verify packages", resources=list(params.evidence))
            )
            return {"result": result.model_dump(mode="json")}
        try:
            reviewed = await anyio.to_thread.run_sync(proposal, params)
        except (ValueError, OSError):
            reviewed = Plan(
                summary="The installed package catalog is unavailable or changed.",
                outcome=Outcome.BLOCKED,
            )
        return {"proposal": reviewed.model_dump(mode="json")}

    async def plan(self, request: CapabilityRequest, context: PluginContext) -> StepPlan:
        params = PackageRequest.model_validate(request.payload)
        if params.evidence is not None or params.inventory:
            raise ValueError("Unexpected readback evidence during planning")
        reviewed = await anyio.to_thread.run_sync(proposal, params)
        if reviewed.outcome not in {Outcome.READY, Outcome.NO_CHANGES}:
            raise ValueError(reviewed.summary)
        return StepPlan(
            step_key=request.step_key,
            executor=context.executor,
            handler="packages",
            capability=request.capability,
            contract_version="1",
            payload=request.payload,
            preconditions={"proposal": reviewed.model_dump(mode="json")},
            resources=tuple(reviewed.resources),
            permissions=frozenset({Permission.ROOT}),
            cancellation=CancellationPolicy.SAFE_BOUNDARY,
            lock_keys=("system.packages",),
        )

    async def execute(self, step: StepPlan, context: PluginContext) -> Receipt:
        return await context.execute_native()

    async def readback(
        self, step: StepPlan, receipt: Receipt | None, context: PluginContext
    ) -> Result:
        return await anyio.to_thread.run_sync(
            observe,
            PackageRequest.model_validate(step.payload),
            Plan.model_validate(step.preconditions["proposal"]),
        )
