"""Validation defaults for immutable application contracts."""

from typing import Any, Self

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )

    def validated(self, **updates: Any) -> Self:
        """Construct a detached and validated revision from declared fields."""
        values = self.model_dump(round_trip=True, by_alias=True)
        for key, value in updates.items():
            field = type(self).model_fields.get(key)
            values[field.alias if field and field.alias else key] = value
        return self.model_validate(values)
