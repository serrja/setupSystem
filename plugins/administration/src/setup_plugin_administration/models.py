"""Validated identity facts for an explicit Debian sudo-group change."""

from pathlib import PurePosixPath
from typing import Annotated, Self

from pydantic import Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator
from setup_system_sdk.files.contracts import ContractModel
from setup_system_sdk.schemas.common import Inputs

ADMIN_REQUEST_BYTES = 32 * 1024


class AdminIntent(ContractModel):
    username: Annotated[StrictStr, Field(pattern=r"^[a-z_][a-z0-9_-]{0,31}$")]

    @field_validator("username")
    @classmethod
    def require_ordinary_name(cls, value: str) -> str:
        if value == "root":
            raise ValueError("Select an existing ordinary login account")
        return value


class GrantConfirmation(AdminIntent):
    before_digest: Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")]
    after_digest: Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")]


class AdminAccount(AdminIntent):
    uid: Annotated[StrictInt, Field(gt=0)]
    gid: Annotated[StrictInt, Field(gt=0)]
    home: StrictStr
    shell: StrictStr
    sudo_gid: Annotated[StrictInt, Field(gt=0)]
    member: StrictBool
    supplementary_gids: Annotated[
        tuple[Annotated[StrictInt, Field(ge=0)], ...], Field(max_length=4096)
    ] = ()

    @model_validator(mode="after")
    def unique_supplementary_groups(self) -> Self:
        if (
            tuple(sorted(set(self.supplementary_gids) - {self.gid, self.sudo_gid}))
            != self.supplementary_gids
        ):
            raise ValueError(
                "Expected distinct additional supplementary group IDs in numeric order"
            )
        return self

    @field_validator("home", "shell")
    @classmethod
    def require_absolute_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            not path.is_absolute()
            or path == PurePosixPath("/")
            or ".." in path.parts
            or str(path) != value
            or not value.isprintable()
            or len(value) > 4096
        ):
            raise ValueError("Expected an absolute local account path")
        return value


class Login(Inputs):
    username: str = Field(
        title="Existing local login",
        min_length=1,
        max_length=32,
        pattern=r"^[a-z_][a-z0-9_-]{0,31}$",
        description="An ordinary account in the local passwd and group databases.",
        json_schema_extra={
            "ui": {"widget": "select", "choice_provider": "accounts", "allow_custom": False}
        },
    )

    @field_validator("username")
    @classmethod
    def ordinary(cls, name: str) -> str:
        if name == "root":
            raise ValueError("Select an ordinary interactive login.")
        return name
