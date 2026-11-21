"""Contracts for the reviewed font release and desktop typography."""

from typing import Annotated, Literal, Self

from pydantic import Field, StrictInt, model_validator

from setup_system.domain.contracts import ContractModel
from setup_system.domain.manifest_types import Digest, Version

Family = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]+$")]
Filename = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]


class FontFile(ContractModel):
    name: Filename
    sha256: Digest


class FontArchive(ContractModel):
    version: Version
    url: str
    sha256: Digest
    files: Annotated[tuple[FontFile, ...], Field(min_length=1, max_length=16)]
    license: Literal["SIL-OFL-1.1"]
    license_file: Filename
    family: Family
    emoji_fallback: Family

    @model_validator(mode="after")
    def verify_source(self) -> Self:
        if (
            self.url
            != f"https://github.com/ryanoasis/nerd-fonts/releases/download/v{self.version}/JetBrainsMono.zip"
        ):
            raise ValueError("Font archive must use its pinned official release URL")
        names = {f.name for f in self.files}
        if len(names) != len(self.files) or self.license_file not in names:
            raise ValueError("Duplicate font filename or absent font license")
        return self

    @property
    def cache_name(self) -> str:
        return f"JetBrainsMono-{self.version}-{self.sha256}.zip"


class Typography(ContractModel):
    ui_size: Annotated[StrictInt, Field(ge=6, le=48)]
    terminal_size: Annotated[StrictInt, Field(ge=6, le=48)]
    panel_size: Annotated[StrictInt, Field(ge=6, le=64)]
    previous_fontconfig_sha256: tuple[Digest, ...] = ()


class FontManifest(ContractModel):
    profile: Typography
    jetbrains_mono_nerd_font: FontArchive


class FontStyle(Typography):
    family: Family
    emoji_fallback: Family
