"""Observed local tools and the Git author fields a profile can change."""

from pathlib import Path

from pydantic import Field, StrictBool, field_validator

from setup_system.domain.contracts import ContractModel


class ShellStatus(ContractModel):
    """Observed host capability; no value is inferred from ``$SHELL``."""

    user: str
    home: Path
    startup_dir: Path
    login_shell: Path
    zsh: Path | None
    registered: StrictBool


class RtkStatus(ContractModel):
    """Observed binary identity and local installation path."""

    path: Path | None
    version: str | None
    product_matches: StrictBool

    def describe(self, pinned_version: str) -> str:
        if self.path is not None and not self.product_matches:
            return "blocked: command named rtk is not rtk-ai/rtk"
        if self.version == pinned_version:
            return "unchanged: pinned RTK is already available"
        if self.path is not None:
            return f"update available: installed {self.version}, pinned {pinned_version}"
        return f"install available: pinned {pinned_version}"


class GitIdentity(ContractModel):
    """Only author fields belong to a profile; authentication stays with Git."""

    name: str = Field(min_length=1)
    email: str = Field(min_length=1)
    signing_key: str | None = None

    @field_validator("name", "email", "signing_key")
    @classmethod
    def single_line(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or any(ord(c) < 32 for c in value)):
            raise ValueError("Identity fields must be nonempty single-line values")
        return value

    def fields(self) -> dict[str, str]:
        values = {"user.name": self.name, "user.email": self.email, "user.useConfigOnly": "true"}
        if self.signing_key is not None:
            values["user.signingKey"] = self.signing_key
        return values
