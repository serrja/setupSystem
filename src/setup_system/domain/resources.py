"""Ownership and consent rules shared by reservation and readback."""

from uuid import UUID

from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.resources import ResourceEffect, ResourceOwnership
from setup_system_sdk.operations import Receipt, Result, StepPlan
from setup_system_sdk.resources import Resource

from setup_system.domain.operations import SUCCESS


def check_reservation(
    plugin: str, row: Resource, owners: dict[str, Resource], *, allow_removal: bool
) -> None:
    prior = owners.get(plugin)
    others = [v for k, v in owners.items() if k != plugin]
    if row.effect == ResourceEffect.REMOVE:
        if others or (prior and set(prior.consumers) - {plugin}):
            raise ValueError("Another plugin requires this resource")
        if not allow_removal and (
            not prior
            or not prior.cleanup
            or prior.ownership != ResourceOwnership.CREATED
            or not prior.after
            or prior.after != row.before
            or prior.remote_id != row.remote_id
        ):
            raise ValueError("Removal requires verified ownership and an unchanged fingerprint")
    elif prior and prior.after and prior.after != row.before:
        raise ValueError("The resource changed after the previous readback")
    elif others and (
        row.ownership not in {ResourceOwnership.SHARED, ResourceOwnership.PREEXISTING}
        or row.before != row.after
        or any(v.after != row.before for v in others)
    ):
        raise ValueError("Another plugin manages this resource")


def holding(observed: Resource, prior: Resource | None) -> Resource:
    if not prior:
        return observed
    return observed.model_copy(
        update={
            "ownership": prior.ownership,
            "before": prior.before,
            "before_permissions": prior.before_permissions,
            "cleanup": prior.cleanup,
            "source_sha256": observed.source_sha256 or prior.source_sha256,
        }
    )


def validate_observation(approved: Resource, applied: Resource | None, observed: Resource) -> None:
    """Bind a verified observation to the concrete identity returned by its executor."""
    approved.validate_observation(observed)
    if applied is None:
        raise ValueError("Restore the executor receipt to establish the origin of this resource")
    applied.validate_observation(observed)
    if observed.effect is ResourceEffect.ENSURE and observed.ownership is ResourceOwnership.CREATED:
        if not applied.after and not applied.remote_id:
            raise ValueError("The executor did not identify the object it created")
        if observed.remote_id is not None and applied.remote_id != observed.remote_id:
            raise ValueError("The created remote identity has no execution evidence")


def validate_readback(
    operation: UUID, step: StepPlan, applied: Receipt | None, result: Result
) -> None:
    observed = Receipt(
        operation_id=operation, step_key=step.step_key, executor=step.executor, result=result
    )
    observed.validate_for(operation, step)
    if applied:
        applied.validate_for(operation, step)
        if (
            any(r.verified for r in result.resources)
            and applied.result.outcome is Outcome.NO_CHANGES
            and any(row.before != row.after for row in applied.result.resources)
        ):
            raise ValueError("A no-change receipt cannot establish a resource transition")
    if result.outcome in SUCCESS:
        if applied is None:
            raise ValueError(
                "Inspect the original execution journal to recover the missing executor receipt"
            )
        if len(result.resources) != len(step.resources) or not all(
            r.verified for r in result.resources
        ):
            raise ValueError("Readback did not verify every approved resource")
    approved = {(r.kind, r.identity): r for r in step.resources}
    executed = (
        {
            (r.kind, r.identity): r
            for r in applied.result.resources
            if applied.result.outcome in SUCCESS or r.verified
        }
        if applied
        else {}
    )
    for row in result.resources:
        if row.verified:
            key = row.kind, row.identity
            validate_observation(approved[key], executed.get(key), row)
