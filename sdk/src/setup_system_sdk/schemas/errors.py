from uuid import UUID

from pydantic import Field

from setup_system_sdk.enums.errors import ErrorCode
from setup_system_sdk.schemas.common import Inputs


class Error(Inputs):
    """A safe diagnostic tied to a field or to the whole operation."""

    code: ErrorCode
    message: str = Field(min_length=1, max_length=512)
    hint: str | None = Field(default=None, min_length=1, max_length=256)
    field: tuple[str | int, ...] = Field(default=(), max_length=32)
    operation_id: UUID | None = None
    diagnostic_id: UUID | None = None
    retryable: bool = False
    context: dict[str, str] = Field(default_factory=dict, max_length=8)
