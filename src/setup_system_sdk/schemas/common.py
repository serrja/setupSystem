from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
ActionId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,79}$")]
EntryName = Annotated[
    str, Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*:[A-Za-z][A-Za-z0-9_]*$")
]
Values = dict[str, JsonValue]


class Inputs(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        allow_inf_nan=False,
        revalidate_instances="always",
    )


class EnvironmentValue(Inputs):
    """Subclasses narrow names, values and scopes for a specific native action."""

    name: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    value: str = Field(max_length=1024)
    scope: Literal["command"] = Field(
        default="command", description="Apply this value only to the selected command."
    )

    @model_validator(mode="after")
    def validate_environment(self) -> Self:
        if (
            self.name
            in {
                "SHELL",
                "PATH",
                "HOME",
                "IFS",
                "ENV",
                "BASH_ENV",
                "GNUMAKEFLAGS",
                "SHELLOPTS",
                "CDPATH",
            }
            or self.name.startswith(
                ("MAKE", "PYTHON", "LD_", "DYLD_", "ANSIBLE_", "BASH", "SETUP_SYSTEM_")
            )
            or "\x00" in self.value
        ):
            raise ValueError("Reserved executor environment")
        return self
