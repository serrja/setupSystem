"""Decode the snapshot formats emitted by the historical ledger writers."""

from typing import Literal
from uuid import UUID

from pydantic import TypeAdapter, ValidationError
from setup_system_sdk.files.manifest_types import Digest
from setup_system_sdk.operations import Mutation
from setup_system_sdk.resources import Resource
from setup_system_sdk.schemas.common import Identifier, Inputs, Values


class ArchivedResources(Inputs):
    resources: tuple[Resource, ...]
    holdings: tuple[Resource, ...]


class NormalizedSnapshot(Inputs):
    revision: Literal[10]
    operation_id: UUID
    release: Digest
    version: str
    source_operation: int
    settings: Values
    inputs: Values


class HistoricalMutation(Inputs):
    operation_id: UUID
    digest: Digest
    plugin_id: Identifier
    mutation: Mutation


def decode_historical(
    raw: str, archive: str | None, operation: UUID, owner: str, release: str, action: str
) -> tuple[Values, ArchivedResources]:
    try:
        body = TypeAdapter(Values).validate_json(raw)
        if body.get("revision") == 10:
            source = NormalizedSnapshot.model_validate(body)
            if (source.operation_id, source.release) != (operation, release) or not archive:
                raise ValueError(
                    "Restore the normalized source database and run its migration again"
                )
            return body, ArchivedResources.model_validate_json(archive)
        if body.get("local") is True:
            raise ValueError(
                "Inspect the original local settings or secret journal with its owning release"
            )
        old = HistoricalMutation.model_validate(body)
        if (old.operation_id, old.plugin_id, old.digest, old.mutation.action_id) != (
            operation,
            owner,
            release,
            action,
        ):
            raise ValueError("Restore the matching original confirmation and database backup")
        return body, ArchivedResources(
            resources=tuple(old.mutation.plan.resources), holdings=old.mutation.holdings
        )
    except ValidationError:
        raise ValueError(
            "Unrecognized historical evidence. Open the preserved backup with its original release."
        ) from None
