"""Run a fixed local Make target with a private, immutable request."""

from __future__ import annotations

import asyncio
import os
import pwd
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Self

import aiofiles
import aiofiles.tempfile
from pydantic import Field, ValidationError, ValidationInfo, field_validator

from setup_system.domain.contracts import MAX_MESSAGE_BYTES, ContractModel
from setup_system.infrastructure.operation_io import read_wire
from setup_system.infrastructure.system_paths import verify_program_tree
from setup_system.make_transport import FOREGROUND as FOREGROUND
from setup_system.make_transport import SYSTEM, Phase, TransportError, execute_make
from setup_system.make_transport import Target as Target
from setup_system.models import RequestEnvelope, Status, TaskResult

INSTALLED = Path("/usr/lib/setup-system")


class BecomePassword:
    """Transfer a single response to Ansible's hidden prompt."""

    def __init__(self, value: str) -> None:
        if (
            not value
            or len(value.encode()) > 1024
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("Enter a non-empty single-line password")
        self._response = value.encode()

    def take(self) -> bytes:
        response, self._response = self._response, b""
        if not response:
            raise RuntimeError("The authorization response has expired")
        return response

    def discard(self) -> None:
        self._response = b""


def resource_root() -> Path:
    """Prefer resources beside the executing module in source and wheel builds."""
    module = Path(__file__).absolute()
    if module.parent.parent.name == "src":
        root = module.parents[2]
    elif module.parent.parent == Path("/usr/lib/python3/dist-packages"):
        root = INSTALLED
    else:
        root = module.parent / "bundle"
    makefile = root / "Makefile"
    if any(parent.is_symlink() for parent in (makefile, *makefile.parents)):
        raise ValueError("Program resources cannot traverse symlinks")
    if makefile.is_file():
        return root
    raise RuntimeError("The configurator resources are missing")


def _environment() -> dict[str, str]:
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    environment = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8"}
    for key in (
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
        "CODEX_HOME",
        "WAYLAND_DISPLAY",
        "HYPRLAND_INSTANCE_SIGNATURE",
        "DISPLAY",
    ):
        if key in os.environ:
            environment[key] = os.environ[key]
    return environment


class Command(ContractModel):
    """A fixed native target and its independently validated, immutable request."""

    target: Target
    request_json: str = Field(max_length=MAX_MESSAGE_BYTES, repr=False)

    @field_validator("request_json", mode="before")
    @classmethod
    def freeze_request(cls, value: object, info: ValidationInfo) -> str:
        target = info.data.get("target")
        if not isinstance(target, Target):
            raise ValueError("Unknown operation target")
        if isinstance(value, str):
            if len(value.encode()) > MAX_MESSAGE_BYTES:
                raise ValueError("Operation request is too large")
            request = RequestEnvelope.model_validate_json(value)
        else:
            request = RequestEnvelope.model_validate(value)
        wire = request.for_target(target.value).model_dump_json(by_alias=True, exclude_unset=True)
        if len(wire.encode()) > MAX_MESSAGE_BYTES:
            raise ValueError("Operation request is too large")
        return wire

    @classmethod
    def capture(cls, target: Target | str, envelope: dict[str, object]) -> Self:
        """Snapshot the wire values before scheduling the operation."""
        return cls.model_validate({"target": target, "request_json": envelope})


class RunEvent(ContractModel):
    target: Target
    phase: Phase

    @property
    def message(self) -> str:
        return {
            Phase.STARTED: "Starting the selected operation.",
            Phase.AUTHENTICATING: "Waiting for the native authorization prompt.",
            Phase.RUNNING: "The native operation is running.",
            Phase.WAITING: "The operation is still active. Waiting for its next safe boundary.",
            Phase.CANCELLING: "Cancellation requested. Waiting for the current safe boundary.",
            Phase.INTERRUPTED: "The command channel was interrupted. Waiting for a safe boundary.",
        }[self.phase]


def _result(path: Path, code: int) -> TaskResult | None:
    try:
        data = read_wire(path)
    except FileNotFoundError:
        return None
    try:
        result = TaskResult.model_validate_json(data)
    except ValidationError:
        raise ValueError("The native operation returned an invalid result") from None
    if code and result.status not in {
        Status.FAILED,
        Status.PARTIAL,
        Status.BLOCKED,
        Status.CANCELLED,
    }:
        raise TransportError(f"Native operation failed with exit status {code}")
    return result


class AsyncMakeRunner:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root

    async def check_authorization(self) -> Path:
        """Identify the packaged program before requesting or transmitting a password."""
        root = self.root if self.root is not None else await asyncio.to_thread(resource_root)
        if root != INSTALLED:
            raise ValueError("System changes require the installed Debian package")
        await asyncio.to_thread(verify_program_tree, root)
        return root

    async def run(
        self,
        command: Command,
        *,
        password: BecomePassword | None = None,
        cancel: asyncio.Event | None = None,
        on_event: Callable[[RunEvent], None] | None = None,
    ) -> TaskResult:
        """Own the operation and its request files until every safe boundary is observed."""
        stop = cancel if cancel is not None else asyncio.Event()
        try:
            if os.geteuid() == 0 or os.geteuid() != os.getuid():
                raise ValueError("Open setup-system as your normal user")
            if stop.is_set():
                return TaskResult(status=Status.CANCELLED)
            command = Command.model_validate(command)
            if command.target in SYSTEM:
                root = await self.check_authorization()
            else:
                root = (
                    self.root if self.root is not None else await asyncio.to_thread(resource_root)
                )
            if (command.target in SYSTEM) != (password is not None):
                raise ValueError("The selected operation has a different authorization requirement")
            job = asyncio.create_task(self._execute(root, command, password, stop, on_event))
            try:
                return await asyncio.shield(job)
            except asyncio.CancelledError:
                stop.set()
                while not job.done():
                    try:
                        await asyncio.shield(job)
                    except asyncio.CancelledError:
                        continue
                job.result()
                raise
        finally:
            if password is not None:
                password.discard()

    async def _execute(
        self,
        root: Path,
        command: Command,
        password: BecomePassword | None,
        stop: asyncio.Event,
        on_event: Callable[[RunEvent], None] | None,
    ) -> TaskResult:
        target = command.target

        def report(phase: Phase) -> None:
            if on_event is not None:
                on_event(RunEvent(target=target, phase=phase))

        env = await asyncio.to_thread(_environment)
        if target in FOREGROUND:
            for key in ("TERM", "COLORTERM"):
                value = os.environ.get(key) or ("xterm-256color" if key == "TERM" else "")
                if (
                    value
                    and len(value) <= 80
                    and all(c.isascii() and (c.isalnum() or c in "-_.+") for c in value)
                ):
                    env[key] = value
        if root != INSTALLED:
            env["SETUP_SYSTEM_INTERPRETER"] = sys.executable
        async with aiofiles.tempfile.TemporaryDirectory(prefix="setup-system-") as temporary:
            stage = Path(temporary)
            request = stage / "request.json"
            result = stage / "result.json"
            async with aiofiles.open(
                request, "xb", opener=lambda p, f: os.open(p, f, 0o600)
            ) as snapshot:
                await snapshot.write(command.request_json.encode())
            env.update(
                SETUP_SYSTEM_REQUEST=str(request),
                SETUP_SYSTEM_RESULT=str(result),
                SETUP_SYSTEM_CANCEL=str(stage / "cancel"),
            )
            if stop.is_set():
                return TaskResult(status=Status.CANCELLED)
            report(Phase.STARTED)
            try:
                code = await execute_make(
                    root,
                    target,
                    env,
                    stop,
                    report,
                    take_password=password.take if password is not None else None,
                )
            except TransportError as exc:
                native_result = await asyncio.to_thread(_result, result, 0)
                if native_result is None:
                    raise
                raise type(exc)(
                    f"{exc}. Recorded native outcome: {native_result.describe()}"
                ) from None
            recorded = await asyncio.to_thread(_result, result, code)
            if recorded is not None:
                return recorded
            if code == 130:
                return TaskResult(status=Status.CANCELLED)
            raise TransportError(
                f"Make exited with status {code} before returning an operation result."
            )
