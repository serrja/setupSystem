"""Session-scoped queries and explicit DML for the operation ledger."""

from collections.abc import Sequence

from setup_system_sdk.operations import Snapshot
from setup_system_sdk.resources import Resource
from sqlalchemy import delete, insert, select, tuple_, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from setup_system.infrastructure.database.models import (
    Change,
    Confirmation,
    Holding,
    NativeReadback,
    Operation,
    OperationStep,
    PlannedRequirement,
    Requirement,
    Reservation,
    Rollback,
)


def resource_rows(operation: str, resources: Sequence[Resource]) -> list[dict[str, str]]:
    return [
        dict(operation=operation, kind=r.kind, identity=r.identity, resource=r.model_dump_json())
        for r in resources
    ]


class OperationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, snapshot: Snapshot) -> None:
        key = str(snapshot.operation_id)
        await self.session.execute(
            insert(Operation).values(
                id=key,
                plugin=snapshot.executor.plugin_id,
                action=snapshot.action_id,
                release=snapshot.executor.release_digest,
                phase="prepared",
            )
        )
        await self.session.execute(
            insert(Confirmation).values(operation=key, snapshot=snapshot.model_dump_json())
        )
        if snapshot.plan.steps:
            await self.session.execute(
                insert(OperationStep),
                [
                    dict(operation=key, step_key=s.step_key, position=i, phase="prepared")
                    for i, s in enumerate(snapshot.plan.steps)
                ],
            )
        if snapshot.plan.resources:
            await self.session.execute(
                insert(Change),
                [{**r, "phase": "prepared"} for r in resource_rows(key, snapshot.plan.resources)],
            )

    async def get(self, key: str) -> Operation:
        return (
            await self.session.execute(select(Operation).where(Operation.id == key))
        ).scalar_one()

    async def legacy(self, key: str) -> tuple[str, str, str | None, str | None]:
        result = await self.session.execute(
            select(
                Operation.plugin, Operation.release, Confirmation.snapshot, Confirmation.recovery
            )
            .outerjoin(Confirmation, Confirmation.operation == Operation.id)
            .where(Operation.id == key)
        )
        owner, digest, snapshot, recovery = result.one()
        return owner, digest, snapshot, recovery

    async def snapshot(self, key: str) -> str:
        return (
            await self.session.execute(
                select(Confirmation.snapshot).where(Confirmation.operation == key)
            )
        ).scalar_one()

    async def steps(self, key: str) -> Sequence[OperationStep]:
        return (
            (
                await self.session.execute(
                    select(OperationStep)
                    .where(OperationStep.operation == key)
                    .order_by(OperationStep.position)
                )
            )
            .scalars()
            .all()
        )

    async def receipt(self, key: str, step_key: str) -> str | None:
        row = await self.session.execute(
            select(OperationStep.receipt).where(
                OperationStep.operation == key, OperationStep.step_key == step_key
            )
        )
        return row.scalar_one()

    async def transition(
        self,
        key: str,
        step_key: str,
        previous: tuple[str, ...],
        phase: str,
        receipt: str | None = None,
    ) -> None:
        statement = (
            update(OperationStep)
            .where(
                OperationStep.operation == key,
                OperationStep.step_key == step_key,
                OperationStep.phase.in_(previous),
            )
            .values(phase=phase)
        )
        if receipt is not None:
            statement = statement.values(receipt=receipt)
        result = await self.session.execute(statement.returning(OperationStep.step_key))
        result.scalar_one()

    async def set_outcome(self, key: str, phase: str, outcome: str | None) -> None:
        await self.session.execute(
            update(Operation).where(Operation.id == key).values(phase=phase, outcome=outcome)
        )

    async def change_phase(self, key: str, resources: Sequence[Resource], phase: str) -> None:
        if resources:
            await self.session.execute(
                update(Change)
                .where(
                    Change.operation == key,
                    tuple_(Change.kind, Change.identity).in_(
                        [(r.kind, r.identity) for r in resources]
                    ),
                )
                .values(phase=phase)
            )

    async def record_evidence(self, key: str, resources: Sequence[Resource]) -> None:
        if resources:
            statement = sqlite_insert(NativeReadback)
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=["operation", "kind", "identity"],
                    set_={"resource": statement.excluded.resource},
                ),
                resource_rows(key, resources),
            )

    async def evidence(self, key: str) -> list[Resource]:
        rows = await self.session.execute(
            select(NativeReadback.resource).where(NativeReadback.operation == key)
        )
        return [Resource.model_validate_json(r) for r in rows.scalars()]

    async def history(
        self, plugin: str | None = None, *, unfinished: bool = False
    ) -> Sequence[Operation]:
        statement = select(Operation)
        if plugin:
            statement = statement.where(Operation.plugin == plugin)
        if unfinished:
            statement = statement.where(Operation.phase.in_(("prepared", "applied", "unresolved")))
        return (
            (await self.session.execute(statement.order_by(Operation.started.desc(), Operation.id)))
            .scalars()
            .all()
        )

    async def set_rollback(self, key: str, phase: str) -> None:
        if phase == "prepared":
            await self.session.execute(insert(Rollback).values(operation=key, phase=phase))
        else:
            result = await self.session.execute(
                update(Rollback)
                .where(Rollback.operation == key, Rollback.phase.in_(("prepared", "unresolved")))
                .values(phase=phase)
                .returning(Rollback.operation)
            )
            result.scalar_one()


class ResourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_holdings(self, plugin_id: str) -> list[Resource]:
        rows = await self.session.execute(
            select(Holding.resource)
            .where(Holding.plugin == plugin_id)
            .order_by(Holding.kind, Holding.identity)
        )
        return [Resource.model_validate_json(raw) for raw in rows.scalars()]

    async def owners(
        self, resources: Sequence[Resource]
    ) -> dict[tuple[str, str], dict[str, Resource]]:
        rows = await self.session.execute(
            select(Holding).where(
                tuple_(Holding.kind, Holding.identity).in_(
                    [(r.kind, r.identity) for r in resources]
                )
            )
        )
        result: dict[tuple[str, str], dict[str, Resource]] = {}
        for item in rows.scalars():
            result.setdefault((item.kind, item.identity), {})[item.plugin] = (
                Resource.model_validate_json(item.resource)
            )
        return result

    async def reserve_many(self, key: str, resources: Sequence[Resource]) -> None:
        if resources:
            await self.session.execute(
                insert(Reservation),
                [dict(operation=key, kind=r.kind, identity=r.identity) for r in resources],
            )

    async def release(self, key: str, resources: Sequence[Resource] | None = None) -> None:
        statement = delete(Reservation).where(Reservation.operation == key)
        if resources is not None:
            statement = statement.where(
                tuple_(Reservation.kind, Reservation.identity).in_(
                    [(r.kind, r.identity) for r in resources]
                )
            )
        await self.session.execute(statement)

    async def save_holdings(self, plugin: str, rows: Sequence[Resource]) -> None:
        if rows:
            insert_row = sqlite_insert(Holding)
            await self.session.execute(
                insert_row.on_conflict_do_update(
                    index_elements=["kind", "identity", "plugin"],
                    set_={"resource": insert_row.excluded.resource},
                ),
                [
                    dict(
                        plugin=plugin,
                        kind=r.kind,
                        identity=r.identity,
                        resource=r.model_dump_json(),
                    )
                    for r in rows
                ],
            )

    async def remove_holdings(self, plugin: str, rows: Sequence[Resource]) -> None:
        if rows:
            await self.session.execute(
                delete(Holding).where(
                    Holding.plugin == plugin,
                    tuple_(Holding.kind, Holding.identity).in_(
                        [(r.kind, r.identity) for r in rows]
                    ),
                )
            )


class DependencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def stage_requirements(self, key: str, consumer: str, providers: Sequence[str]) -> None:
        if providers:
            await self.session.execute(
                insert(PlannedRequirement),
                [dict(operation=key, consumer=consumer, provider=p) for p in providers],
            )

    async def removals(self, providers: Sequence[str]) -> set[str]:
        rows = await self.session.execute(
            select(Operation.plugin).where(
                Operation.plugin.in_(providers),
                Operation.action == "uninstall",
                Operation.phase.in_(("prepared", "applied", "unresolved")),
            )
        )
        return set(rows.scalars())

    async def dependents(self, plugin: str) -> set[str]:
        rows = await self.session.execute(
            select(Requirement.consumer)
            .where(Requirement.provider == plugin)
            .union(select(PlannedRequirement.consumer).where(PlannedRequirement.provider == plugin))
        )
        return set(rows.scalars())

    async def finish(self, key: str, consumer: str, *, confirm: bool, uninstall: bool) -> None:
        if confirm:
            await self.session.execute(
                sqlite_insert(Requirement)
                .from_select(
                    ["consumer", "provider"],
                    select(PlannedRequirement.consumer, PlannedRequirement.provider).where(
                        PlannedRequirement.operation == key
                    ),
                )
                .on_conflict_do_nothing()
            )
            if uninstall:
                await self.session.execute(
                    delete(Requirement).where(Requirement.consumer == consumer)
                )
        await self.session.execute(
            delete(PlannedRequirement).where(PlannedRequirement.operation == key)
        )
