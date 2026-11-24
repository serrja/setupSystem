"""Keep the latest signed APT source check independent of desired application state."""

import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, Field

from setup_system.domain.contracts import ContractModel
from setup_system.infrastructure.user_storage import (
    atomic_write,
    checked_directory,
    safe_file,
    state_root,
)


class SourceAttempt(ContractModel):
    checked: AwareDatetime
    result: Literal["verified", "failed"]
    reason: str = Field(max_length=120)

    def describe(self) -> str:
        return f"{self.checked.isoformat()} — {self.reason}"


def observe(ok: bool, diagnostic: str) -> SourceAttempt:
    """Return a bounded public reason without retaining downloaded text or URLs."""
    http = re.search(
        r"\b([45][0-9]{2})\s+(?:Forbidden|Unauthorized|Not Found|"
        r"Too Many Requests|Bad Gateway|Service Unavailable)",
        diagnostic,
        re.I,
    )
    if ok:
        reason = "Fresh signed package indexes verified"
    elif http:
        reason = f"Package indexes unavailable (HTTP {http[1]})"
    else:
        reason = "Could not retrieve and verify all signed package indexes"
    return SourceAttempt(
        checked=datetime.now(UTC), result="verified" if ok else "failed", reason=reason
    )


def _path() -> Path:
    path = checked_directory(state_root()) / "last-chatgpt-source.json"
    safe_file(path, os.getuid())
    return path


def remember(record: SourceAttempt) -> None:
    atomic_write(_path(), record.model_dump_json().encode(), preserve_existing_mode=False)


def previous() -> str:
    path = _path()
    if not path.exists():
        return "No recorded Apply source check"
    if path.stat().st_size > 2048:
        raise ValueError("Unrecognized source check record")
    return SourceAttempt.model_validate_json(path.read_bytes()).describe()
