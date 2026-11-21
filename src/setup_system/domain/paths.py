"""Absolute user paths shared by a confirmed plan and its system commands."""

from pathlib import Path

from pydantic import field_validator

from setup_system.domain.contracts import ContractModel


class XdgPaths(ContractModel):
    config: Path
    data: Path
    state: Path
    cache: Path

    @field_validator("*")
    @classmethod
    def absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute() or ".." in value.parts or any(ord(c) < 32 for c in str(value)):
            raise ValueError("Expected an absolute XDG path without traversal")
        return value

    def overrides(self) -> dict[str, Path]:
        return {"config": self.config, "data": self.data, "state": self.state, "cache": self.cache}
