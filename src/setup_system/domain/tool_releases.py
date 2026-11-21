"""Pinned release contracts for explicitly selected user tools."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from setup_system.domain.contracts import ContractModel
from setup_system.domain.manifest_types import Digest, Version


class CodexArchive(ContractModel):
    url: str
    member: str
    sha256: Digest


class CodexRelease(ContractModel):
    source: str
    verification: str
    version: Version
    amd64: CodexArchive
    arm64: CodexArchive

    @model_validator(mode="after")
    def official_assets(self) -> Self:
        origin = "https://github.com/openai/codex/releases"
        tag = f"rust-v{self.version}"
        if self.source != f"{origin}/tag/{tag}":
            raise ValueError("Unexpected Codex release")
        for arch, archive in (("x86_64", self.amd64), ("aarch64", self.arm64)):
            member = f"codex-{arch}-unknown-linux-musl"
            if (
                archive.member != member
                or archive.url != f"{origin}/download/{tag}/{member}.tar.gz"
            ):
                raise ValueError("Unexpected official Codex archive")
        return self

    def archive(self, arch: str) -> CodexArchive:
        if arch == "amd64":
            return self.amd64
        if arch == "arm64":
            return self.arm64
        raise ValueError("No reviewed Codex artifact for the target")


class RtkRelease(ContractModel):
    product: Literal["rtk-ai/rtk"]
    version: Version
    edition: Literal["full"]
    release: str
    checksums: Literal["checksums.txt"]
    linux_x86_64: Literal["rtk-x86_64-unknown-linux-musl.tar.gz"]
    linux_arm64: Literal["rtk-aarch64-unknown-linux-gnu.tar.gz"]

    @model_validator(mode="after")
    def official_release(self) -> Self:
        if self.release != f"https://github.com/rtk-ai/rtk/releases/download/v{self.version}":
            raise ValueError("Unexpected official RTK release")
        return self


class AcquisitionReport(ContractModel):
    app: Literal["chatgpt", "obsidian"]
    version: Annotated[str, Field(min_length=1, max_length=120)]
    architecture: Literal["amd64", "arm64"]
    time: Annotated[str, Field(min_length=1, max_length=64)]
    result: Annotated[str, Field(min_length=1, max_length=700, pattern=r"^[^\p{Cc}]+$")]

    @field_validator("time")
    @classmethod
    def recorded_at(cls, value: str) -> str:
        if datetime.fromisoformat(value).utcoffset() is None:
            raise ValueError("Acquisition time must include its UTC offset")
        return value
