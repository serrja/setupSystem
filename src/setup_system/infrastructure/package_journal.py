"""Preserve recognized historical package receipts before a new transaction starts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field

from setup_system.domain.contracts import ContractModel
from setup_system.domain.package_journal import InstallJournal
from setup_system.infrastructure.system_files import validate_system_path, write_system


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


def prepare_journal(path: Path) -> None:
    """Reject unknown receipts and retain the original v1 record by digest."""
    validate_system_path(path)
    try:
        details = path.lstat()
    except FileNotFoundError:
        return
    if details.st_nlink != 1 or details.st_size > 1024 * 1024:
        raise ValueError("Unsafe installation journal")
    content = path.read_bytes()
    try:
        document = json.loads(content)
        if not isinstance(document, dict):
            raise ValueError
        if document.get("schema") == 2:
            InstallJournal.model_validate_json(content, strict=True)
            return
        HistoricalJournal.model_validate_json(content, strict=True)
    except ValueError:
        raise ValueError(
            "Unrecognized installation journal; inspect the original receipt"
        ) from None
    backup = (
        path.parent / "migration" / ("install-v1-" + hashlib.sha256(content).hexdigest() + ".json")
    )
    validate_system_path(backup)
    if backup.exists() and backup.read_bytes() != content:
        raise ValueError("Installation journal migration backup conflicts with the original")
    write_system(backup, content)
