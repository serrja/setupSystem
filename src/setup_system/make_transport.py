"""Supervise native Make processes and the single hidden Ansible prompt."""

from __future__ import annotations

import asyncio
import os
import re
import select
import signal
import termios
from collections.abc import Awaitable, Callable
from contextlib import ExitStack, suppress
from enum import StrEnum
from pathlib import Path

import pexpect  # type: ignore[import-untyped]
from pexpect.fdpexpect import fdspawn  # type: ignore[import-untyped]

from setup_system.configuration import ApplicationOptions


class Phase(StrEnum):
    STARTED = "started"
    AUTHENTICATING = "authenticating"
    RUNNING = "running"
    WAITING = "waiting"
    CANCELLING = "cancelling"
    INTERRUPTED = "interrupted"


class TransportError(RuntimeError):
    """A failure of the local command channel, without its potentially sensitive transcript."""


class AuthorizationError(TransportError):
    """The native authentication channel refused the provided interaction."""


class ReadinessTimeoutError(TransportError):
    """The expected native prompt or read-only response did not arrive in time."""


type Report = Callable[[Phase], None]


async def _supervise(
    work: Awaitable[int], cancel: asyncio.Event, stop: Callable[[], Awaitable[None]]
) -> int:
    """Run the cancellation callback once and retain ownership until the process ends."""
    operation = asyncio.ensure_future(work)
    requested = asyncio.create_task(cancel.wait())
    try:
        await asyncio.wait((requested, operation), return_when=asyncio.FIRST_COMPLETED)
        if requested.done() and not operation.done():
            await stop()
        return await operation
    finally:
        requested.cancel()
        with suppress(asyncio.CancelledError):
            await requested
        if not operation.done():
            await operation


def _cancel_at_boundary(env: dict[str, str]) -> None:
    # Existing markers are already effective. Never follow or touch their targets.
    with suppress(FileExistsError):
        os.close(os.open(env["SETUP_SYSTEM_CANCEL"], os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))


def _signal_group(pid: int, sig: signal.Signals) -> None:
    with suppress(ProcessLookupError):
        os.killpg(pid, sig)


async def _stop_group(pid: int, done: asyncio.Task[int], grace: float) -> None:
    _signal_group(pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(asyncio.shield(done), grace)
    except TimeoutError:
        _signal_group(pid, signal.SIGKILL)
        await done
    else:
        # A recipe can leave descendants alive even after Make exits.
        _signal_group(pid, signal.SIGKILL)


def _drain_output(fd: int) -> None:
    """Keep servicing a live transaction if its Pexpect reader is lost."""
    while True:
        try:
            select.select([fd], [], [])
            if not os.read(fd, 65536):
                return
        except BlockingIOError:
            continue


async def authenticated_make(
    root: Path,
    args: list[str],
    env: dict[str, str],
    cancel: asyncio.Event,
    report: Report,
    take_password: Callable[[], bytes],
    *,
    options: ApplicationOptions,
    root_account: bool,
) -> int:
    # The new session has no controlling terminal. getpass reads the private
    # terminal on stdin and reports its prompt on stderr. Keeping output on a
    # pipe lets us hang up input without disrupting a package writer.
    with ExitStack() as descriptors:
        try:
            with ExitStack() as child_ends:
                master, slave = os.openpty()
                terminal_input = descriptors.enter_context(os.fdopen(master, "wb", buffering=0))
                child_ends.callback(os.close, slave)
                output, writer = os.pipe()
                descriptors.callback(os.close, output)
                child_ends.callback(os.close, writer)
                process = await asyncio.create_subprocess_exec(
                    "/usr/bin/make",
                    *args,
                    cwd=root,
                    env=env,
                    stdin=slave,
                    stdout=writer,
                    stderr=writer,
                    start_new_session=True,
                )
        except OSError:
            raise TransportError("Could not open the native authorization terminal") from None
        reader = None
        authorized = False
        reached_eof = False
        finished = asyncio.create_task(process.wait())

        async def request_stop() -> None:
            report(Phase.CANCELLING)
            if authorized:
                terminal_input.close()
                await asyncio.to_thread(_cancel_at_boundary, env)
            else:
                await _stop_group(process.pid, finished, options.termination_grace_seconds)

        async def observe() -> int:
            nonlocal authorized, reader, reached_eof
            # Pexpect's asyncio transport closes its pipe at EOF. Keep the original
            # output pipe under our ownership until the child and its transaction finish.
            reader = fdspawn(os.dup(output), timeout=None)
            account = b"SU" if root_account else b"SUDO"
            prompt = re.compile(rb"(?m)^(?:BECOME|" + account + rb") password: \Z")
            report(Phase.AUTHENTICATING)
            # Bound Pexpect's unmatched pre-authentication transcript.
            matched = await reader.expect(
                [prompt, pexpect.EOF, re.compile(rb"(?s).{65536}")],
                timeout=options.prompt_timeout_seconds,
                async_=True,
            )
            reached_eof = matched == 1
            if cancel.is_set():
                return 130
            if reached_eof:
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(finished), options.prompt_timeout_seconds
                    )
                except TimeoutError:
                    raise ReadinessTimeoutError(
                        "The authorization terminal closed before the command was ready"
                    ) from None
            if matched != 0:
                raise AuthorizationError("Unexpected output before the native authorization prompt")
            if termios.tcgetattr(master)[3] & termios.ECHO:
                raise AuthorizationError("Password prompt has terminal echo enabled")
            # No await separates the echo check, single PTY write and ownership transition.
            # The native line reader can proceed only after the terminating newline.
            answer = take_password()
            try:
                if os.write(master, answer + b"\n") != len(answer) + 1:
                    raise TransportError(
                        "The native terminal could not accept the complete response"
                    )
            finally:
                del answer
            authorized = True
            report(Phase.RUNNING)
            # Match real output, not periodic wakeups. The reader retains at most a
            # single chunk. After a quiet period wait for output or EOF without a timer.
            timeout: float | None = options.idle_timeout_seconds
            tail = b""
            while True:
                index = await reader.expect(
                    [pexpect.EOF, re.compile(rb"(?s).{1,8192}"), pexpect.TIMEOUT],
                    timeout=timeout,
                    async_=True,
                )
                if index == 0:
                    terminal_input.close()
                    reached_eof = True
                    return await finished
                if index == 2:
                    terminal_input.close()
                    report(Phase.WAITING)
                    timeout = None
                else:
                    timeout = options.idle_timeout_seconds
                    tail = (tail + reader.after)[-512:]
                    if tail.split(b"\n")[-1] in (
                        b"BECOME password: ",
                        account + b" password: ",
                        b"Verification code: ",
                    ):
                        raise AuthorizationError("Additional native authentication is unsupported")

        failure: TransportError
        try:
            return await _supervise(observe(), cancel, request_stop)
        except pexpect.TIMEOUT:
            failure = ReadinessTimeoutError(
                "The native authorization prompt did not arrive in time"
            )
        except (OSError, termios.error, pexpect.ExceptionPexpect):
            # Some Pexpect exceptions carry a terminal transcript. Do not retain
            # them on the public exception, even as a suppressed __context__.
            failure = TransportError(
                "Authorization transport failed; inspect the operation result"
                if authorized
                else "The native authorization prompt was unavailable"
            )
        finally:
            try:
                if authorized and not reached_eof:
                    terminal_input.close()
                    try:
                        report(Phase.INTERRUPTED)
                        await asyncio.to_thread(_cancel_at_boundary, env)
                    finally:
                        with suppress(OSError):
                            await asyncio.to_thread(_drain_output, output)
                elif not authorized and not finished.done():
                    await _stop_group(process.pid, finished, options.termination_grace_seconds)
            finally:
                try:
                    await finished
                finally:
                    try:
                        # After the child exits, consume hangup so Pexpect releases its
                        # asyncio reader before the descriptor number can be reused.
                        if reader is not None and reader.isalive() and not reached_eof:
                            with suppress(OSError, pexpect.ExceptionPexpect):
                                await reader.expect(
                                    pexpect.EOF,
                                    timeout=options.termination_grace_seconds,
                                    async_=True,
                                )
                    finally:
                        if reader is not None:
                            with suppress(OSError):
                                reader.close()
        raise failure
