"""Persist the most recently recorded mutation outcome without request or terminal contents."""

import asyncio
import fcntl
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from pydantic import AwareDatetime, model_validator

from setup_system.domain.contracts import ContractModel
from setup_system.infrastructure.operation_io import read_wire
from setup_system.infrastructure.user_storage import atomic_write, private_directory, state_root
from setup_system.make_transport import Target
from setup_system.models import Status

_NAME = "last-operation.json"


class OperationOutcome(ContractModel):
    target: Target
    status: Status
    recorded_at: AwareDatetime

    @model_validator(mode="after")
    def require_mutation(self) -> Self:
        if not self.target.mutating or self.status.allows_confirmation:
            raise ValueError("Expected the final outcome of a mutating target")
        return self

    def describe(self) -> str:
        return f"Last operation: {self.target.value} — {self.status.value}."


def _read(directory: int) -> OperationOutcome | None:
    try:
        wire = read_wire(Path("/proc/self/fd") / str(directory) / _NAME, limit=512, private=True)
    except FileNotFoundError:
        return None
    return OperationOutcome.model_validate_json(wire, strict=True)


def _previous() -> OperationOutcome | None:
    try:
        with private_directory(state_root(), create=False) as directory:
            return _read(directory)
    except FileNotFoundError:
        return None


async def read_last_outcome() -> OperationOutcome | None:
    return await asyncio.to_thread(_previous)


def _publish(target: str, status: Status) -> None:
    if os.getuid() == 0 or os.geteuid() != os.getuid():
        raise ValueError("Record the operation outcome as the invoking user")
    record = OperationOutcome(target=Target(target), status=status, recorded_at=datetime.now(UTC))
    path = state_root()
    with private_directory(path) as directory:
        fcntl.flock(directory, fcntl.LOCK_EX)
        _read(directory)
        atomic_write(
            path / _NAME, (record.model_dump_json() + "\n").encode(), preserve_existing_mode=False
        )


async def record_outcome(target: str, status: Status) -> None:
    """Finish publication before propagating cancellation to the operation owner."""
    writer = asyncio.create_task(asyncio.to_thread(_publish, target, status))
    cancelled = False
    while not writer.done():
        try:
            await asyncio.shield(writer)
        except asyncio.CancelledError:
            cancelled = True
    writer.result()
    if cancelled:
        raise asyncio.CancelledError
