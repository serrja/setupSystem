"""Obtain operation-bound authorization from the foreground interface."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from pydantic import SecretBytes

from setup_system.infrastructure.trust import TrustedExecution


@dataclass(frozen=True)
class AuthorizationRequest:
    operation_id: UUID
    step_key: str
    executor: TrustedExecution


type Authorize = Callable[[AuthorizationRequest], Awaitable[SecretBytes | None]]
