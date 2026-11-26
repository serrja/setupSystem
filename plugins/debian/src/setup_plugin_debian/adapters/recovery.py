"""Reinspect package versions using the retained executor observations."""

from typing import ClassVar

import anyio
from pydantic import TypeAdapter
from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.operations import Plan, Receipt, Result, StepPlan
from setup_system_sdk.providers import CapabilityRequest
from setup_system_sdk.schemas.common import Inputs, Values

from setup_plugin_debian.adapters import apt
from setup_plugin_debian.adapters.layout import (
    INSTALLED,
    require_installed_resource,
    verify_installation,
)
from setup_plugin_debian.models import RecoveryQuery


def inspect_record(request: RecoveryQuery) -> Result:
    snapshot = request.snapshot
    if snapshot.get("revision") == 10:
        matches = snapshot.get("release") == request.release_digest
    else:
        matches = (snapshot.get("digest"), snapshot.get("plugin_id")) == (
            request.release_digest,
            request.owner,
        )
    if not matches or snapshot.get("operation_id") != request.operation:
        return Result(
            outcome=Outcome.UNKNOWN_OUTCOME,
            message="Restore the matching release and original confirmation.",
        )
    expected = {(r.kind, r.identity) for r in request.approved}
    if (
        not expected
        or expected != {(r.kind, r.identity) for r in request.native}
        or not all(
            r.verified
            and r.kind == "package"
            and r.remote_id is None
            and r.permissions is None
            and r.before_permissions is None
            for r in request.native
        )
    ):
        return Result(
            outcome=Outcome.UNKNOWN_OUTCOME,
            message="Use the original executor to inspect non-package or missing evidence.",
        )
    attestation = INSTALLED / "history" / request.owner / request.release_digest
    if attestation.is_file():
        require_installed_resource(attestation, str(attestation.relative_to(INSTALLED)))
    else:
        # Independent readback uses the installed provider and the ledger's native observations.
        verify_installation(INSTALLED)
    for row in request.native:
        TypeAdapter(apt.PackageName).validate_python(row.identity)
    result = apt.verify_packages(
        Plan(summary="Inspect retained package versions", resources=list(request.native))
    )
    if result.outcome is not Outcome.COMPLETE:
        return Result(
            outcome=Outcome.UNKNOWN_OUTCOME,
            message="Inspect the installed versions and the package journal before confirming.",
        )
    return result


class RecoveryProvider:
    request_model: ClassVar[type[Inputs]] = RecoveryQuery

    async def inspect(self, request: CapabilityRequest, context: PluginContext) -> Values:
        return {
            "result": (
                await anyio.to_thread.run_sync(
                    inspect_record, RecoveryQuery.model_validate(request.payload)
                )
            ).model_dump(mode="json")
        }

    async def plan(self, request: CapabilityRequest, context: PluginContext) -> StepPlan:
        raise ValueError("Inspect historical evidence before selecting a separate recovery action")

    async def execute(self, step: StepPlan, context: PluginContext) -> Receipt:
        raise ValueError("Historical readback cannot execute a mutation")

    async def readback(
        self, step: StepPlan, receipt: Receipt | None, context: PluginContext
    ) -> Result:
        return await anyio.to_thread.run_sync(
            inspect_record, RecoveryQuery.model_validate(step.payload)
        )
