"""Run bounded requests in the selected release's private interpreter."""

import asyncio
import os
import signal
from contextlib import suppress
from pathlib import Path

from pydantic import ValidationError

from setup_system.errors import OperationError
from setup_system.execution.authorization import session_environment, ssh_socket
from setup_system.execution.make_transport import guard_refusal
from setup_system.services.extension_lifecycle import generation, generation_lock
from setup_system.services.extensions import Activation
from setup_system.settings import Settings
from setup_system_sdk.enums.errors import ErrorCode
from setup_system_sdk.enums.permissions import Permission
from setup_system_sdk.schemas.errors import Error
from setup_system_sdk.schemas.wire import LIMIT, Reply, Request


def worker_environment(state: Activation, action_id: str | None) -> dict[str, str]:
    env = session_environment()
    if any(
        item.id == action_id and Permission.ACCOUNT_KEYS in item.permissions
        for item in state.manifest.actions
    ):
        env.update(ssh_socket())
    return env


async def pipe(
    argv: list[str], payload: bytes, env: dict[str, str], cwd: Path, timeout: float | None
) -> bytes:
    if len(payload) > LIMIT:
        raise OperationError(Error(code=ErrorCode.INVALID_REQUEST, message="Request is too large."))
    child = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    assert child.stdin and child.stdout and child.stderr

    async def send(stream: asyncio.StreamWriter) -> None:
        try:
            stream.write(payload)
            await stream.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            stream.close()

    async def consume(stream: asyncio.StreamReader, retain: bool) -> tuple[bytes, int]:
        data = bytearray()
        length = 0
        while chunk := await stream.read(16384):
            length += len(chunk)
            if retain and length <= LIMIT:
                data.extend(chunk)
        return bytes(data), length

    try:
        async with asyncio.timeout(timeout), asyncio.TaskGroup() as tasks:
            tasks.create_task(send(child.stdin))
            stdout = tasks.create_task(consume(child.stdout, True))
            stderr = tasks.create_task(consume(child.stderr, False))
            status = tasks.create_task(child.wait())
        raw, length = stdout.result()
        code = status.result()
        if blocked := guard_refusal(code, argv):
            return blocked.model_dump_json().encode()
        if code or length > LIMIT:
            raise OperationError(
                Error(
                    code=ErrorCode.WORKER_FAILED,
                    message="The process exited before returning a valid response."
                    if code
                    else "The process response exceeds the protocol size limit.",
                    context={"exit_code": str(code), "stderr_bytes": str(stderr.result()[1])},
                )
            )
        return raw
    finally:
        if child.returncode is None:
            if timeout is not None:
                with suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
            await child.wait()


async def query(state: Activation, request: Request, options: Settings) -> Reply:
    if state.manifest.id != request.plugin_id:
        raise OperationError(
            Error(
                code=ErrorCode.INCOMPATIBLE_PLUGIN,
                message="The requested release identity has changed.",
            )
        )
    try:
        with generation_lock(generation(state), exclusive=False):
            raw = await pipe(
                [str(state.python), "-I", "-B", "-m", "setup_system_sdk.worker"],
                request.model_dump_json().encode(),
                {
                    **worker_environment(state, request.action_id),
                    **{
                        k: os.environ[k]
                        for k in state.manifest.environment_fields
                        if k in os.environ
                    },
                },
                state.resources,
                options.read_only_timeout_seconds,
            )
        reply = Reply.model_validate_json(raw)
    except TimeoutError:
        raise OperationError(
            Error(
                code=ErrorCode.WORKER_TIMEOUT,
                message="The extension did not finish before its time limit.",
                operation_id=request.operation_id,
                retryable=True,
            )
        ) from None
    except ValidationError:
        raise OperationError(
            Error(
                code=ErrorCode.INCOMPATIBLE_PLUGIN,
                message="The worker response does not match this SDK version.",
                operation_id=request.operation_id,
            )
        ) from None
    except OperationError as exc:
        raise OperationError(
            exc.detail.model_copy(update={"operation_id": request.operation_id})
        ) from None
    except OSError:
        raise OperationError(
            Error(
                code=ErrorCode.WORKER_FAILED,
                message="The extension interpreter could not be started.",
                operation_id=request.operation_id,
            )
        ) from None
    if reply.error:
        raise OperationError(reply.error)
    return reply
