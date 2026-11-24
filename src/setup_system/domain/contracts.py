"""Validation defaults for immutable application contracts."""

from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator

MAX_MESSAGE_BYTES = 1 << 20


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
        values = self.model_dump(round_trip=True, by_alias=True)
        for key, value in updates.items():
            field = type(self).model_fields.get(key)
            values[field.alias if field and field.alias else key] = value
        return self.model_validate(values)
