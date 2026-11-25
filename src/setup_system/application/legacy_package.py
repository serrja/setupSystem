"""Decode the public stage and status from a pre-plugin package journal for offline recovery."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field

from setup_system.domain.contracts import ContractModel


class HistoricalJournal(ContractModel):
    # Published schema 1 admitted additional observations. Preserve the original bytes.
    model_config = ConfigDict(extra="allow")
    schema_version: Literal[1] = Field(alias="schema")
    status: Literal[
        "running", "prepared", "packages-complete", "system-complete", "failed", "complete"
    ]
    stage: Literal[
        "metadata",
        "resolution",
        "downloads",
        "user-prefetch",
        "packages",
        "source",
        "happ-service",
        "user-runtime",
        "user",
        "system-desktop",
        "user-downloads",
        "temporary-network",
        "package-downloads",
        "temporary-network-cleanup",
    ]
    completed: tuple[str, ...] = ()
    error: str | None = None


class HistoricalV2(ContractModel):
    schema_version: Literal[2] = Field(alias="schema")
    status: Literal["running", "prepared", "packages-complete", "system-complete", "failed"]
    stage: Literal["metadata", "resolution", "downloads", "packages", "source", "happ-service"]
    completed: tuple[
        Literal["metadata", "resolution", "downloads", "packages", "source", "happ-service"], ...
    ] = ()
    error: str | None = None


def historical_status(
    path: Path = Path("/var/lib/setup-system/install.json"), *, owner: int = 0
) -> str:
    """Return only validated public stage names. Preserve all original bytes."""
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != owner
                or info.st_nlink != 1
                or info.st_mode & 0o022
            ):
                raise ValueError
            data = stream.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ValueError
        for schema in (HistoricalJournal, HistoricalV2):
            try:
                row = schema.model_validate_json(data, strict=True)
            except ValueError:
                continue
            return f"{row.status} ({row.stage})"
        raise ValueError
    except FileNotFoundError:
        return "No previous package journal"
    except (OSError, ValueError):
        return "Previous package journal unavailable or unrecognized"
