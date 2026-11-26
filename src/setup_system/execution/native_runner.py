"""Execute the fixed Make target attached to a confirmed action."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import Field

from setup_system.execution.authorization import BecomePassword, _environment
from setup_system.execution.encryption import validate_home_access
from setup_system.execution.make_transport import (
    Phase,
    authenticated_make,
    guard_refusal,
    make_command,
)
from setup_system.execution.operation_io import read_wire
from setup_system.execution.recipes.user_terminal import PERMISSION as NATIVE_TERMINAL_PERMISSION
from setup_system.execution.worker import pipe, worker_environment
from setup_system.layout import INSTALLED, verify_installation
from setup_system.services.extensions import Activation, builtins
from setup_system.settings import Settings
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.permissions import Permission
from setup_system_sdk.schemas.common import ActionId, Identifier, Inputs
from setup_system_sdk.schemas.manifest import Action
from setup_system_sdk.schemas.operation import Mutation, Result
from setup_system_sdk.schemas.wire import LIMIT


class Snapshot(Inputs):
    operation_id: UUID = Field(default_factory=uuid4)
    digest: str
    plugin_id: Identifier
    settings_revision: str
    mutation: Mutation = Field(repr=False)


def descriptor(state: Activation, action_id: ActionId) -> Action:
    for action in state.manifest.actions:
        if action.id == action_id:
            return action
    raise ValueError("The installed plugin does not declare this action")


def protected_resources(state: Activation) -> Path:
    verify_installation(INSTALLED)
    item = next(
        (
            entry
            for entry in builtins(INSTALLED)
            if entry.manifest == state.manifest
            and entry.release
            and entry.release.digest == state.digest
        ),
        None,
    )
    if item is None:
        raise ValueError("Administrative actions require the exact Debian-installed plugin release")
    root = (
        INSTALLED
        / "plugins"
        / state.manifest.id
        / state.manifest.entry_point.partition(":")[0].partition(".")[0]
    )
    if not all(p.is_file() for p in (INSTALLED / "sdk/environment.mk", root / "Makefile")):
        raise ValueError("The protected Make resources are incomplete")
    return root


class NativeRunner:
    def __init__(self, options: Settings) -> None:
        self.options = options

    async def execute(
        self,
        state: Activation,
        snapshot: Snapshot,
        password: BecomePassword | None,
        cancel: asyncio.Event,
    ) -> Result:
        action = descriptor(state, snapshot.mutation.action_id)
        if action.target is None:
            raise ValueError("This action has no mutation target")
        privileged = bool(
            {Permission.ROOT, Permission.SUDO, Permission.SU} & set(action.permissions)
        )
        if privileged != (password is not None):
            raise ValueError("The action requires a different authorization context")
        root = (
            await asyncio.to_thread(protected_resources, state) if privileged else state.resources
        )
        env = (
            _environment()
            if privileged
            else await asyncio.to_thread(worker_environment, state, action.id)
        )
        env["SETUP_SYSTEM_INTERPRETER"] = "/usr/bin/python3" if privileged else str(state.python)
        makefiles = [
            str(
                INSTALLED / "sdk/environment.mk"
                if privileged
                else Path(__file__).parent / "recipes/environment.mk"
            ),
            str(root / "Makefile"),
        ]
        args = [
            "-rR",
            "-s",
            "-j1",
            "--no-print-directory",
            "-f",
            makefiles[0],
            "-f",
            makefiles[1],
            "--",
            action.target,
        ]
        wire = snapshot.mutation.model_dump_json().encode()
        if not privileged:
            await asyncio.to_thread(validate_home_access, Path.home())
            timeout = self.options.idle_timeout_seconds
            if NATIVE_TERMINAL_PERMISSION in action.permissions:
                env["SETUP_SYSTEM_NATIVE_TERMINAL"] = "1"
                timeout = self.options.terminal_timeout_seconds
            raw = await pipe(make_command(args), wire, env, root, timeout)
            return Result.model_validate_json(raw)
        assert password
        with tempfile.TemporaryDirectory() as temporary:
            request = Path(temporary) / "request.json"
            result = request.with_name("result.json")

            def stage() -> None:
                with os.fdopen(
                    os.open(request, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
                ) as stream:
                    stream.write(wire)

            await asyncio.to_thread(stage)
            env.update(
                SETUP_SYSTEM_RELEASE_ID=state.manifest.id,
                SETUP_SYSTEM_RELEASE_DIGEST=snapshot.digest,
                SETUP_SYSTEM_REQUEST=str(request),
                SETUP_SYSTEM_RESULT=str(result),
                SETUP_SYSTEM_CANCEL=str(request.with_name("cancel")),
            )
            started = False

            def phase(value: Phase) -> None:
                nonlocal started
                if value is Phase.RUNNING:
                    started = True

            code = await authenticated_make(
                root,
                args,
                env,
                cancel,
                phase,
                password.take,
                options=self.options,
                root_account=Permission.SU in action.permissions,
            )
            blocked = guard_refusal(code, make_command(args, privileged=True))
            if not started and blocked is not None:
                return blocked
            if not result.exists() and not started:
                return Result(
                    outcome=Outcome.BLOCKED, message="Authorization or preflight did not complete"
                )
            native = Result.model_validate_json(
                await asyncio.to_thread(read_wire, result, limit=LIMIT, private=True)
            )
            if code and native.outcome in (Outcome.CHANGED, Outcome.COMPLETE, Outcome.NO_CHANGES):
                raise ValueError("Native transaction and result disagree")
            return native
