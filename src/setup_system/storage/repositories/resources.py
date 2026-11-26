"""Persist immutable observations and evaluate ownership at reservation time."""

from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from setup_system.storage.models import (
    Change,
    Consumer,
    Holding,
    Observation,
    Operation,
    ResourceKey,
)
from setup_system_sdk.enums.resources import ResourceEffect, ResourceOwnership
from setup_system_sdk.schemas.operation import Resource


async def observations(db: AsyncSession, ids: Collection[int]) -> dict[int, Resource]:
    if not ids:
        return {}
    rows = await db.execute(
        select(Observation, ResourceKey)
        .join(ResourceKey, ResourceKey.id == Observation.resource)
        .where(Observation.id.in_(ids))
    )
    consumers: dict[int, list[str]] = {}
    for observation, plugin in await db.execute(
        select(Consumer.observation, Consumer.plugin_id)
        .where(Consumer.observation.in_(ids))
        .order_by(Consumer.observation, Consumer.position)
    ):
        consumers.setdefault(observation, []).append(plugin)
    return {
        obs.id: Resource(
            kind=key.kind,
            identity=key.identity,
            before=obs.before,
            after=obs.after,
            source_sha256=obs.source_sha256,
            verified=obs.verified,
            ownership=obs.ownership,
            effect=obs.effect,
            cleanup=obs.cleanup,
            remote_id=obs.remote_id,
            before_permissions=obs.before_permissions,
            permissions=obs.permissions,
            consumers=tuple(consumers.get(obs.id, ())),
        )
        for obs, key in rows
    }


async def save_observation(db: AsyncSession, resource: int, item: Resource) -> int:
    row = Observation(
        resource=resource,
        verified=item.verified,
        ownership=item.ownership,
        effect=item.effect,
        cleanup=item.cleanup,
        before=item.before,
        after=item.after,
        before_permissions=item.before_permissions,
        permissions=item.permissions,
        remote_id=item.remote_id,
        source_sha256=item.source_sha256,
    )
    db.add(row)
    await db.flush()
    db.add_all(
        Consumer(observation=row.id, plugin_id=plugin, position=index)
        for index, plugin in enumerate(item.consumers)
    )
    return row.id


async def holding_rows(db: AsyncSession, plugin: str) -> list[Holding]:
    return list(
        await db.scalars(
            select(Holding)
            .join(ResourceKey)
            .where(Holding.plugin_id == plugin)
            .order_by(ResourceKey.kind, ResourceKey.identity)
        )
    )


async def resource_key(db: AsyncSession, desired: Resource) -> ResourceKey:
    key = (
        await db.scalars(
            select(ResourceKey).where(
                ResourceKey.kind == desired.kind, ResourceKey.identity == desired.identity
            )
        )
    ).one_or_none()
    if key is None:
        key = ResourceKey(kind=desired.kind, identity=desired.identity)
        db.add(key)
        await db.flush()
    return key


async def reserve_identity(
    db: AsyncSession, plugin: str, desired: Resource, *, allow_preexisting_removal: bool
) -> int:
    key = await resource_key(db, desired)
    holdings = list(await db.scalars(select(Holding).where(Holding.resource == key.id)))
    observed = await observations(db, [row.observation for row in holdings])
    owners = {row.plugin_id: observed[row.observation] for row in holdings}
    own = owners.pop(plugin, None)
    if desired.effect is ResourceEffect.REMOVE:
        if owners or (own and set(own.consumers) - {plugin}):
            raise ValueError("A consumer still uses the resource")
        if own and (
            own.after != desired.before
            or own.remote_id != desired.remote_id
            or own.permissions != desired.before_permissions
        ):
            raise ValueError("Removal conflicts with the last verified observation")
        if not allow_preexisting_removal and (
            own is None
            or not own.cleanup
            or own.ownership is not ResourceOwnership.CREATED
            or not own.after
            or not own.verified
        ):
            raise ValueError("Cleanup requires an unchanged resource created by this extension")
    else:
        if own and own.after != desired.before:
            raise ValueError("A managed resource changed since the previous observation")
        if owners and (
            desired.ownership not in (ResourceOwnership.PREEXISTING, ResourceOwnership.SHARED)
            or desired.before != desired.after
            or any(item.after != desired.before for item in owners.values())
        ):
            raise ValueError("Another extension owns this resource")
        if (
            own is None
            and desired.ownership is ResourceOwnership.CREATED
            and desired.before is not None
        ):
            raise ValueError("An existing resource cannot acquire created ownership")
    return key.id


async def confirm_readback(
    db: AsyncSession,
    op: Operation,
    plugin: str,
    change: Change,
    desired: Resource,
    native: Resource | None,
    actual: Resource,
) -> None:
    if actual.ownership != desired.ownership or actual.effect != desired.effect:
        raise ValueError("Readback differs from the approved ownership or effect")
    for source in (desired, native):
        if source is not None and any(
            value is not None and value != observed
            for value, observed in (
                (source.after, actual.after),
                (source.permissions, actual.permissions),
                (source.remote_id, actual.remote_id),
                (source.source_sha256, actual.source_sha256),
            )
        ):
            raise ValueError("The resource differs from its approved identity")
    if desired.effect is ResourceEffect.REMOVE and actual.after is not None:
        raise ValueError("Removal is missing its absence readback")
    if (
        desired.ownership is ResourceOwnership.CREATED
        and desired.cleanup
        and desired.effect is ResourceEffect.ENSURE
        and not (
            (desired.after is not None or (native is not None and native.after is not None))
            and (
                actual.remote_id is None
                or desired.remote_id == actual.remote_id
                or (native is not None and native.remote_id == actual.remote_id)
            )
        )
    ):
        raise ValueError("Missing native evidence identifying the created resource")
    change.confirmed = await save_observation(db, change.resource, actual)
    holding = await db.get(Holding, (change.resource, plugin))
    if desired.effect is ResourceEffect.REMOVE:
        if holding:
            await db.delete(holding)
    else:
        held = desired.model_copy(
            update={
                "after": actual.after,
                "remote_id": actual.remote_id,
                "permissions": actual.permissions,
                "verified": True,
            }
        )
        if holding:
            previous = (await observations(db, [holding.observation]))[holding.observation]
            held = held.model_copy(
                update={
                    "ownership": previous.ownership,
                    "before": previous.before,
                    "before_permissions": previous.before_permissions,
                    "cleanup": previous.cleanup,
                    "source_sha256": held.source_sha256 or previous.source_sha256,
                }
            )
        oid = await save_observation(db, change.resource, held)
        if holding:
            holding.observation = oid
        else:
            db.add(Holding(plugin_id=plugin, resource=change.resource, observation=oid))
