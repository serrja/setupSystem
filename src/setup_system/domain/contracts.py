"""Validation defaults for immutable application contracts."""

from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )

    @field_validator("schema_version", mode="before", check_fields=False)
    @classmethod
    def require_integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Expected an integer schema version")
        return value

    def validated(self, **updates: object) -> Self:
        """Construct a detached and validated revision from declared fields."""
        values = self.model_dump(round_trip=True, by_alias=False)
        return self.model_validate({**values, **updates}, by_name=True, by_alias=False)
