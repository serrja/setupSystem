"""Interpret historical package evidence only for authenticated installed releases."""

from typing import ClassVar

import anyio
from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.operations import Mutation, Receipt, Result, StepPlan
from setup_system_sdk.providers import CapabilityRequest
from setup_system_sdk.schemas.common import Inputs, Values

from setup_plugin_debian.adapters import apt
from setup_plugin_debian.adapters.layout import INSTALLED, require_installed_resource
from setup_plugin_debian.models import RecoveryQuery


def inspect_record(request: RecoveryQuery) -> Result:
    unresolved = Result(
        outcome=Outcome.UNKNOWN_OUTCOME, message="Historical execution could not be authenticated."
    )
    path = INSTALLED / "history" / request.owner / request.release_digest
    if (
        len(request.release_digest) != 64
        or not all(c in "0123456789abcdef" for c in request.release_digest)
        or not path.is_file()
    ):
        return unresolved
    require_installed_resource(path, str(path.relative_to(INSTALLED)))
    if (
        request.snapshot.get("digest"),
        request.snapshot.get("plugin_id"),
        request.snapshot.get("operation_id"),
    ) != (request.release_digest, request.owner, request.operation):
        return unresolved
    operation = Mutation.model_validate(request.snapshot.get("mutation"))
    if not operation.plan.resources or any(r.kind != "package" for r in operation.plan.resources):
        return unresolved
    result = apt.verify_packages(operation.plan)
    return result if result.outcome is Outcome.COMPLETE else unresolved


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
