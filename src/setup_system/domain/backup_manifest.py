"""Backup manifests written by the source handoff and v0.4.0 applications."""

from typing import Annotated, Literal, Self

from pydantic import Field, StrictInt, model_validator

from setup_system.domain.contracts import ContractModel
from setup_system.domain.manifest_types import Digest
from setup_system.domain.receipts import RunId


class BackupItem(ContractModel):
    source: str
    home_relative: str | None = None
    backup: str
    sha256: Digest
    mode: Annotated[StrictInt, Field(ge=0, le=0o777)]
    kind: Literal["sqliteSnapshot"] | None = None


class BackupManifest(ContractModel):
    run_id: RunId
    items: tuple[BackupItem, ...]
    moves: tuple[()]
    application: Literal["happ"] | None = None

    @model_validator(mode="after")
    def distinct_originals(self) -> Self:
        if len({item.source for item in self.items}) != len(self.items):
            raise ValueError("Duplicate backup targets")
        if any(item.kind and self.application != "happ" for item in self.items):
            raise ValueError("A database snapshot requires a Happ backup manifest")
        return self
