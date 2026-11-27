"""Obtain operation-bound authorization from the foreground interface."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

import anyio
from pydantic import SecretBytes

from setup_system.infrastructure.trust import TrustedExecution


@dataclass(frozen=True)
class AuthorizationRequest:
    operation_id: UUID
    step_key: str
    executor: TrustedExecution


type Authorize = Callable[[AuthorizationRequest], Awaitable[SecretBytes | None]]


async def before_execution[T](work: Awaitable[T], cancelled: anyio.Event) -> T | None:
    """Stop waiting for a read-only preparation or foreground prompt when its owner cancels."""
    result = None
    failure = None
    async with anyio.create_task_group() as tasks:

        async def stop() -> None:
            await cancelled.wait()
            tasks.cancel_scope.cancel()

        tasks.start_soon(stop)
        try:
            result = await work
        except Exception as error:
            failure = error
        finally:
            tasks.cancel_scope.cancel()
    if failure is not None:
        raise failure
    return result
