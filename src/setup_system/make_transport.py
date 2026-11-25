"""Supervise native Make processes and the single hidden Ansible prompt."""

from __future__ import annotations

import asyncio
import os
import re
import select
import signal
import sys
import termios
from collections.abc import Awaitable, Callable
from contextlib import ExitStack, suppress
from enum import StrEnum
from pathlib import Path

import pexpect  # type: ignore[import-untyped]
from pexpect.fdpexpect import fdspawn  # type: ignore[import-untyped]

PROMPT_TIMEOUT = 30.0
IDLE_TIMEOUT = 90.0
TERMINATE_TIMEOUT = 2.0


class Target(StrEnum):
    ADMIN_INSPECT = "admin-inspect"
    ADMIN_PLAN = "admin-plan"
    ADMIN_APPLY = "admin-apply"
    BASE_INSPECT = "base-inspect"
    BASE_PLAN = "base-plan"
    BASE_APPLY = "base-apply"
    BASE_USER_APPLY = "base-user-apply"
    WORKSPACE_INSPECT = "workspace-inspect"
    WORKSPACE_PLAN = "workspace-plan"
    WORKSPACE_APPLY = "workspace-apply"
    WORKSPACE_USER_APPLY = "workspace-user-apply"
    CLEANUP_PLAN = "cleanup-plan"
    CLEANUP_APPLY = "cleanup-apply"
    RESTORE_PLAN = "restore-plan"
    RESTORE_APPLY = "restore-apply"
    DISPLAY_PREVIEW = "display-preview"
    PROJECT_CHECK = "project-check"
    PROJECT_LIST = "project-list"
    TEMPLATE_VERSIONS = "template-versions"
    GIT_INSPECT = "git-inspect"
    PROJECT_EDITOR = "project-editor"
    PROJECT_CODEX = "project-codex"
    PROJECT_PREPARE_PLAN = "project-prepare-plan"
    PROJECT_PREPARE_APPLY = "project-prepare-apply"
    TEMPLATE_CACHE_PLAN = "template-cache-plan"
    TEMPLATE_CACHE_APPLY = "template-cache-apply"
    TEMPLATE_CREATE_PLAN = "template-create-plan"
    TEMPLATE_CREATE_APPLY = "template-create-apply"
    TEMPLATE_UPDATE_PLAN = "template-update-plan"
    TEMPLATE_UPDATE_APPLY = "template-update-apply"
    GIT_CONFIGURE_PLAN = "git-configure-plan"
    GIT_CONFIGURE_APPLY = "git-configure-apply"
    GIT_USE_PLAN = "git-use-plan"
    GIT_USE_APPLY = "git-use-apply"
    SHELL_CONFIGURE_PLAN = "shell-configure-plan"
    SHELL_CONFIGURE_APPLY = "shell-configure-apply"
    RTK_INSTALL_PLAN = "rtk-install-plan"
    RTK_INSTALL_APPLY = "rtk-install-apply"

    @property
    def mutating(self) -> bool:
        return self.value.endswith("-apply") or self is Target.DISPLAY_PREVIEW


FOREGROUND = frozenset({Target.PROJECT_EDITOR, Target.PROJECT_CODEX})
SYSTEM = frozenset(
    {Target.BASE_APPLY, Target.WORKSPACE_APPLY, Target.CLEANUP_APPLY, Target.ADMIN_APPLY}
)


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


async def _stop_group(pid: int, done: asyncio.Task[int]) -> None:
    _signal_group(pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(asyncio.shield(done), TERMINATE_TIMEOUT)
    except TimeoutError:
        _signal_group(pid, signal.SIGKILL)
        await done
    else:
        # A recipe can leave descendants alive even after Make exits.
        _signal_group(pid, signal.SIGKILL)


async def _plain(
    root: Path,
    args: list[str],
    env: dict[str, str],
    cancel: asyncio.Event,
    report: Report,
    *,
    mutating: bool,
    foreground: bool,
) -> int:
    if foreground and (not sys.stdin.isatty() or not sys.stdout.isatty()):
        raise ValueError("Opening the project requires an interactive terminal")
    read_only = not (mutating or foreground)
    output = asyncio.subprocess.PIPE if read_only else asyncio.subprocess.DEVNULL
    try:
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/make",
            *args,
            cwd=root,
            env=env,
            stdin=None if foreground else asyncio.subprocess.DEVNULL,
            stdout=None if foreground else output,
            stderr=None if foreground else asyncio.subprocess.STDOUT,
            start_new_session=not foreground,
        )
    except OSError:
        raise TransportError("Could not start the packaged Make operation") from None
    finished = asyncio.create_task(process.wait())

    async def observe() -> int:
        report(Phase.RUNNING)
        if process.stdout is not None:
            # Discard output as it arrives; command output is never a user-visible diagnostic.
            try:
                while await asyncio.wait_for(process.stdout.read(65536), IDLE_TIMEOUT):
                    pass
                return await asyncio.wait_for(asyncio.shield(finished), IDLE_TIMEOUT)
            except TimeoutError:
                raise ReadinessTimeoutError(
                    "The read-only operation stopped reporting activity"
                ) from None
            except OSError:
                raise TransportError("Lost the read-only command output channel") from None
        try:
            return await asyncio.wait_for(asyncio.shield(finished), IDLE_TIMEOUT)
        except TimeoutError:
            report(Phase.WAITING)
            return await finished

    async def request_stop() -> None:
        report(Phase.CANCELLING)
        if read_only:
            await _stop_group(process.pid, finished)
        else:
            await asyncio.to_thread(_cancel_at_boundary, env)

    try:
        code = await _supervise(observe(), cancel, request_stop)
        return 130 if read_only and cancel.is_set() else code
    finally:
        if read_only and not finished.done():
            await _stop_group(process.pid, finished)
        await finished


def _drain_output(fd: int) -> None:
    """Keep servicing a live transaction if its Pexpect reader is lost."""
    while True:
        try:
            select.select([fd], [], [])
            if not os.read(fd, 65536):
                return
        except BlockingIOError:
            continue


async def _authenticated(
    root: Path,
    args: list[str],
    env: dict[str, str],
    cancel: asyncio.Event,
    report: Report,
    take_password: Callable[[], bytes],
    *,
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
                await _stop_group(process.pid, finished)

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
                timeout=PROMPT_TIMEOUT,
                async_=True,
            )
            reached_eof = matched == 1
            if cancel.is_set():
                return 130
            if reached_eof:
                try:
                    return await asyncio.wait_for(asyncio.shield(finished), PROMPT_TIMEOUT)
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
            timeout: float | None = IDLE_TIMEOUT
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
                    timeout = IDLE_TIMEOUT
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
                    await _stop_group(process.pid, finished)
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
                                    pexpect.EOF, timeout=TERMINATE_TIMEOUT, async_=True
                                )
                    finally:
                        if reader is not None:
                            with suppress(OSError):
                                reader.close()
        raise failure


async def execute_make(
    root: Path,
    target: Target,
    env: dict[str, str],
    cancel: asyncio.Event,
    report: Report,
    *,
    take_password: Callable[[], bytes] | None,
) -> int:
    target = Target(target)
    if (target in SYSTEM) != (take_password is not None):
        raise ValueError("The selected target has a different authorization requirement")
    args = ["-rR", "-j1", "--no-print-directory", "-f", str(root / "Makefile"), target.value]
    if take_password is not None:
        return await _authenticated(
            root,
            args,
            env,
            cancel,
            report,
            take_password,
            root_account=target == Target.ADMIN_APPLY,
        )
    return await _plain(
        root,
        args,
        env,
        cancel,
        report,
        mutating=target.mutating,
        foreground=target in FOREGROUND,
    )
