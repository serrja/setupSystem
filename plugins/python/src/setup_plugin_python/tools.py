"""Stage an immutable user tool release and publish only its reviewed executable wrappers."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shlex
import subprocess
import tempfile
import uuid
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from platformdirs import user_data_path

from setup_plugin_python.enums import ToolAction
from setup_system.execution import retirement_checkpoint as checkpoint
from setup_system.execution.file_publication import move_new, observe
from setup_system.execution.managed_files import apply_files
from setup_system.execution.processes import run_command
from setup_system.execution.recipes.apt import DebianPackages
from setup_system.execution.recorded_tree import record_tree, remove_recorded
from setup_system.execution.user_storage import (
    checked_directory,
    inside_home,
    private_directory,
    safe_file,
)
from setup_system.services.extension_lifecycle import fingerprint, generation_lock, seal_generation
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.resources import ResourceOwnership
from setup_system_sdk.plugin import PluginSettings, owned_resources
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan, Resource, Result


def execute(argv: list[str], stage: Path, *, capture: bool = False) -> str:
    """Run a reviewed tool command with temporary manager configuration."""
    result = run_command(
        argv,
        cwd=stage,
        timeout=180,
        stdin=subprocess.DEVNULL,
        discard_stderr=True,
        discard_output=not capture,
        max_output_bytes=1024 * 1024,
        check=False,
        env={
            "HOME": str(stage),
            "TMPDIR": str(stage),
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "UV_NO_CONFIG": "1",
            "UV_NO_PROGRESS": "1",
        },
    )
    if result.returncode:
        raise ValueError("The tool manager could not complete the reviewed action")
    return result.stdout


class Removal(Inputs):
    generation: str
    files: dict[str, tuple[int, int, int, str]]


def _checked_generation(path: Path, digest: str | None) -> Removal | None:
    if checkpoint.pending(path):
        saved = checkpoint.load(path, ".removing.json")
        checkpoint.check_target(path, saved)
        record = Removal.model_validate_json(saved.payload)
        if record.generation != digest or path.name != digest:
            raise ValueError("Tool generation retirement belongs to a different release")
        return record
    checked_directory(path)
    if path.name != digest:
        raise ValueError("Tool generation differs from its installed identity")
    marker = path / ".removing.json"
    if os.path.lexists(marker):
        safe_file(marker, os.getuid())
        with os.fdopen(
            os.open(marker, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW), "rb"
        ) as stream:
            data = stream.read(64 * 1024 * 1024 + 1)
        if len(data) > 64 * 1024 * 1024:
            raise ValueError("Tool retirement record exceeds the limit")
        record = Removal.model_validate_json(data)
        if record.generation != digest or any(
            record.files.get(key) != entry for key, entry in record_tree(path, marker).items()
        ):
            raise ValueError("A tool generation changed during cleanup")
        return record
    seal = path / "generation.sha256"
    if (
        seal.is_symlink()
        or not seal.is_file()
        or seal.stat().st_size != 64
        or fingerprint(path) != seal.read_text()
    ):
        raise ValueError("A tool generation changed after publication")
    return None


def _remove_generation(path: Path, digest: str | None) -> None:
    if checkpoint.pending(path):
        record = _checked_generation(path, digest)
        assert record
        checkpoint.finish(path, ".removing.json", record.model_dump_json().encode())
        return
    with generation_lock(path, exclusive=True):
        marker = path / ".removing.json"
        record = _checked_generation(path, digest)
        if record is None:
            record = Removal(generation=path.name, files=record_tree(path))
            _checked_generation(path, digest)
            checkpoint.publish(path, marker.name, record.model_dump_json().encode(), path.parent)
        remove_recorded(path, record.files, marker)
        checkpoint.finish(path, marker.name, record.model_dump_json().encode())


def _remove_wrapper(path: Path, row: Resource) -> None:
    holding = path.with_name(".setup-system-retire-" + uuid.uuid4().hex)
    move_new(path, holding)
    try:
        observed = observe(holding)
        if observed is None or observed.sha256 != row.before or observed.mode != row.permissions:
            raise ValueError("Command file changed during cleanup")
        holding.unlink()
    except (OSError, ValueError):
        if holding.exists():
            move_new(holding, path)
        raise


class Toolchain(DebianPackages):
    """Keep generations under one plugin and expose fixed tools to the user's editor."""

    plugin_id: ClassVar[str]
    binaries: ClassVar[dict[str, str]]
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        LifecycleAction.CHECK: Inputs,
        LifecycleAction.CONFIGURE: Inputs,
        ToolAction.REMOVE: Inputs,
    }

    def lock(self, stage: Path, settings: PluginSettings) -> bytes:
        raise NotImplementedError

    def install_tools(self, stage: Path, folder: Path, lock: bytes) -> None:
        raise NotImplementedError

    def storage(self) -> Path:
        return inside_home(user_data_path("setup-system") / "toolchains" / self.plugin_id)

    def tool_binaries(self, settings: PluginSettings) -> dict[str, str]:
        return self.binaries

    def links(self, generation: Path, settings: PluginSettings) -> dict[Path, bytes]:
        links = {}
        for name, binary in self.tool_binaries(settings).items():
            executable = shlex.quote(str(generation / binary))
            body = f'#!/bin/sh\nexport PYTHONDONTWRITEBYTECODE=1\nexec {executable} "$@"\n'
            links[inside_home(Path.home() / ".local/bin" / name)] = body.encode()
        return links

    def status(self, settings: PluginSettings) -> Plan:
        names = self.tool_binaries(settings)
        root = self.storage()
        rows = [
            r
            for r in owned_resources()
            if r.kind == "toolchain_generation"
            and Path(r.identity).parent == root
            and r.ownership == "created"
        ]
        for row in rows:
            folder = Path(row.identity)
            checked_directory(folder)
            if self.verify_generation(folder, settings) and all(
                (state := observe(link)) is not None
                and state.sha256 == hashlib.sha256(body).hexdigest()
                for link, body in self.links(folder, settings).items()
            ):
                return Plan(
                    summary="Configured tools are available",
                    outcome=Outcome.NO_CHANGES,
                    resources=[row],
                )
        return Plan(
            summary="Configure the selected tools: " + ", ".join(names),
            outcome=Outcome.NEEDS_USER_ACTION,
        )

    async def inspect(self, settings: PluginSettings) -> Result:
        plan = await asyncio.to_thread(self.status, settings)
        return Result(outcome=plan.outcome, message=plan.summary, resources=plan.resources)

    def verify_generation(self, folder: Path, settings: PluginSettings) -> bool:
        return all(
            (folder / binary).is_file()
            and os.access(folder / binary, os.X_OK)
            and (folder / binary).resolve().is_relative_to(folder)
            for binary in self.tool_binaries(settings).values()
        )

    def _plan(self, lock: bytes, settings: PluginSettings) -> Plan:
        digest = hashlib.sha256(lock).hexdigest()
        root = self.storage()
        checked_directory(root)
        folder = root / digest
        owned = {r.identity: r for r in owned_resources() if r.ownership == "created"}
        if checkpoint.pending(folder):
            raise ValueError("Finish the interrupted tool generation retirement")
        exists = folder.exists() or folder.is_symlink()
        if exists:
            checked_directory(folder)
            if str(folder) not in owned or not self.verify_generation(folder, settings):
                raise ValueError("Existing tool path needs explicit recovery before a change")
            seal = folder / "generation.sha256"
            if (
                not seal.is_file()
                or seal.is_symlink()
                or seal.stat().st_size != 64
                or fingerprint(folder) != seal.read_text()
            ):
                raise ValueError("Installed tool generation has local changes")
        changed = not exists
        resources = [
            Resource(
                kind="toolchain_generation",
                identity=str(folder),
                before=digest if exists else None,
                after=digest,
                ownership=ResourceOwnership.CREATED,
                cleanup=True,
                permissions=0o700,
            )
        ]
        for path, content in self.links(folder, settings).items():
            checked_directory(path.parent)
            old = observe(path)
            if old and (str(path) not in owned or owned[str(path)].after != old.sha256):
                raise ValueError(
                    "An existing or edited tool path is outside this plugin's ownership"
                )
            after = hashlib.sha256(content).hexdigest()
            changed |= not old or old.sha256 != after
            resources.append(
                Resource(
                    kind="file",
                    identity=str(path),
                    ownership=ResourceOwnership.CREATED,
                    before=old.sha256 if old else None,
                    after=after,
                    permissions=0o755 if path.parent.name == "bin" else 0o600,
                    cleanup=True,
                )
            )
        return Plan(
            summary="Publish the verified tool generation and selected command paths",
            warnings=["Existing tool generations are retained for recovery."],
            resources=resources,
            outcome=Outcome.READY if changed else Outcome.NO_CHANGES,
        )

    def remove_plan(self) -> Plan:
        root = self.storage()
        allowed = {inside_home(Path.home() / ".local/bin" / name) for name in self.binaries}
        allowed.add(root / "modules.txt")
        rows = []
        for item in owned_resources():
            if item.ownership != "created" or not item.cleanup or item.consumers:
                continue
            path = (
                inside_home(Path(item.identity))
                if item.kind in {"file", "toolchain_generation"}
                else None
            )
            if path is None:
                continue
            if item.kind == "file":
                if path not in allowed:
                    continue
                state = observe(path)
                if state is None:
                    continue
                if state.sha256 != item.after or state.mode != item.permissions:
                    raise ValueError("A managed command changed after publication")
            else:
                if path.parent != root or not re.fullmatch(r"[a-f0-9]{64}", path.name):
                    raise ValueError("Tool generation path does not belong to this plugin")
                if not os.path.lexists(path) and not checkpoint.pending(path):
                    continue
                _checked_generation(path, item.after)
            rows.append(
                item.model_copy(update={"effect": "remove", "before": item.after, "after": None})
            )
        rows.sort(key=lambda r: (r.kind != "file", r.identity))
        return Plan(
            summary="Remove the listed command files and unchanged private tool generations",
            warnings=["Close tools using these generations before removal."],
            resources=rows,
            outcome=Outcome.READY if rows else Outcome.NO_CHANGES,
        )

    def remove(self, plan: Plan) -> Result:
        if self.remove_plan() != plan:
            raise ValueError("Owned tool state changed before cleanup")
        done: list[Resource] = []
        try:
            for row in plan.resources:
                path = Path(row.identity)
                if row.kind == "file":
                    _remove_wrapper(path, row)
                else:
                    _remove_generation(path, row.before)
                done.append(row.model_copy(update={"verified": True}))
        except (OSError, ValueError):
            return Result(
                outcome=Outcome.PARTIAL,
                message="Inspect changed tool resources before resuming",
                resources=done,
            )
        return Result(outcome=Outcome.CHANGED, message="Owned tools removed", resources=done)

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id == LifecycleAction.CHECK:
            return self.status(settings)
        if action_id == ToolAction.REMOVE:
            return self.remove_plan()
        if action_id != LifecycleAction.CONFIGURE:
            return super().prepare(action_id, settings, inputs)
        with tempfile.TemporaryDirectory() as stage:
            return self._plan(self.lock(Path(stage), settings), settings)

    def mutate(
        self, action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan
    ) -> Result:
        if action_id == ToolAction.REMOVE:
            return self.remove(plan)
        if action_id != LifecycleAction.CONFIGURE:
            raise ValueError("Unknown toolchain mutation")
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            lock = self.lock(stage, settings)
            if self._plan(lock, settings) != plan:
                return Result(
                    outcome=Outcome.BLOCKED, message="Tool release or command path changed"
                )
            first, *links = plan.resources
            folder = Path(first.identity)
            if first.before is None:
                with private_directory(folder.parent) as directory:
                    with checkpoint.allow_creation(folder):
                        os.mkdir(folder.name, mode=0o700, dir_fd=directory)
                try:
                    self.install_tools(stage, folder, lock)
                    if not self.verify_generation(folder, settings):
                        raise ValueError("One or more tools failed executable readback")
                    seal_generation(folder)
                except (OSError, ValueError, subprocess.SubprocessError):
                    return Result(
                        outcome=Outcome.PARTIAL, message="Inspect the staged tool generation"
                    )
            desired = self.links(folder, settings)
            old = {p: observe(p) for p in desired}
            if any(
                (prior.sha256 if prior else None) != row.before
                for prior, row in zip(old.values(), links, strict=True)
            ):
                return Result(
                    outcome=Outcome.PARTIAL, message="A command path changed during installation"
                )
            receipt = apply_files(
                desired,
                expected=old,
                modes={
                    Path(r.identity): r.permissions
                    for r in links
                    if r.before is None and r.permissions is not None
                },
            )
            return Result(
                outcome=Outcome.CHANGED,
                message="Tool generation published",
                resources=plan.resources,
                recovery_id=receipt.name,
            )

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        if plan.resources and all(
            r.effect == "remove" and r.kind in {"file", "toolchain_generation"}
            for r in plan.resources
        ):
            good = await asyncio.to_thread(
                lambda: all(
                    not os.path.lexists(r.identity)
                    and not (
                        r.kind == "toolchain_generation" and checkpoint.pending(Path(r.identity))
                    )
                    for r in plan.resources
                )
            )
            return Result.observed(
                outcome=Outcome.COMPLETE if good else Outcome.PARTIAL,
                message="Removal readback complete" if good else "A target still exists",
                resources=plan.resources,
            )
        if not plan.resources or plan.resources[0].kind != "toolchain_generation":
            return await super().verify(plan, settings, inputs)

        def matches() -> bool:
            first, *links = plan.resources
            if not self.verify_generation(Path(first.identity), settings):
                return False
            for row in links:
                current = observe(Path(row.identity))
                if not current or current.sha256 != row.after or current.mode != row.permissions:
                    return False
            return True

        good = await asyncio.to_thread(matches)
        return Result.observed(
            outcome=Outcome.COMPLETE if good else Outcome.PARTIAL,
            resources=plan.resources,
            message="Toolchain readback complete"
            if good
            else "The installed tools differ from the confirmed plan",
        )
