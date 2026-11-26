"""Ownership and consent rules shared by reservation and readback."""

from setup_system_sdk.enums.resources import ResourceEffect, ResourceOwnership
from setup_system_sdk.resources import Resource


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
            "cleanup": prior.cleanup,
            "source_sha256": observed.source_sha256 or prior.source_sha256,
        }
    )
