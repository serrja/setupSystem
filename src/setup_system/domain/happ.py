"""Validated pins from the official architecture-specific Happ release."""

from __future__ import annotations

import re

from pydantic import Field, model_validator

from setup_system.domain.contracts import ContractModel
from setup_system.domain.manifest_types import Digest, FrozenMap


class HappAsset(ContractModel):
    url: str
    sha256: Digest
    architecture: str = Field(alias="packageArchitecture")


class HappManifest(ContractModel):
    version: str = Field(pattern=r"^[0-9]+(?:\.[0-9]+)+$")
    package_version: str = Field(alias="packageVersion", pattern=r"^[A-Za-z0-9.+:~_-]{1,200}$")
    source: str
    publisher: str
    verification: str
    linux: FrozenMap[str, HappAsset]

    @model_validator(mode="after")
    def check_release(self) -> HappManifest:
        base = "https://github.com/Happ-proxy/happ-desktop/releases/"
        if self.source != base + "tag/" + self.version:
            raise ValueError("Happ metadata must reference the official release")
        for arch, row in self.linux.items():
            if row.architecture != arch or not row.url.startswith(
                base + "download/" + self.version + "/"
            ):
                raise ValueError("Happ architecture or upstream release URL differs from the pin")
            if not re.fullmatch(
                r"[A-Za-z0-9_.-]+\.deb", row.url.rsplit("/", 1)[-1]
            ) or "/" in row.url.removeprefix(base + "download/" + self.version + "/"):
                raise ValueError("Invalid Happ release asset")
        return self


class HappPin(ContractModel):
    digest: Digest
    version: str
    architecture: str
    url: str

    def validate_metadata(self, output: str) -> None:
        fields: dict[str, str] = {}
        for line in output.splitlines():
            name, separator, value = line.partition(":")
            if not separator or name in fields:
                raise ValueError("Happ archive metadata differs from the pinned release")
            fields[name] = value.strip()
        if fields != {
            "Package": "happ",
            "Version": self.version,
            "Architecture": self.architecture,
        }:
            raise ValueError("Happ archive metadata differs from the pinned release")

    def cache_name(self) -> str:
        return f"happ_{self.version}_{self.architecture}.deb"
