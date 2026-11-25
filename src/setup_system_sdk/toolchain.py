"""Stage an immutable user tool release and publish only its reviewed executable wrappers."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import ClassVar

from platformdirs import user_data_path

from setup_system.extension_lifecycle import fingerprint, generation_lock, seal_generation
from setup_system.infrastructure.file_publication import move_new, observe
from setup_system.infrastructure.managed_files import apply_files
from setup_system.infrastructure.processes import run_command
from setup_system.infrastructure.user_storage import (
    checked_directory,
    inside_home,
    private_directory,
)
from setup_system_sdk import (
    Inputs,
    Outcome,
    Plan,
    PluginSettings,
    Resource,
    Result,
    owned_resources,
)
from setup_system_sdk.apt import DebianPackages


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
            "NPM_CONFIG_USERCONFIG": str(stage / "npm-user.rc"),
            "NPM_CONFIG_GLOBALCONFIG": str(stage / "npm-global.rc"),
            "NPM_CONFIG_ENGINE_STRICT": "true",
            "NPM_CONFIG_CACHE": str(stage / "cache"),
            "UV_NO_CONFIG": "1",
            "UV_NO_PROGRESS": "1",
        },
    )
    if result.returncode:
        raise ValueError("The tool manager could not complete the reviewed action")
    return result.stdout


class Toolchain(DebianPackages):
    """Keep generations under one plugin and expose fixed tools to the user's editor."""

    plugin_id: ClassVar[str]
    binaries: ClassVar[dict[str, str]]
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        "check": Inputs,
        "configure": Inputs,
        "remove-tools": Inputs,
    }

    def lock(self, stage: Path, settings: PluginSettings) -> bytes:
        raise NotImplementedError

    def install_tools(self, stage: Path, folder: Path, lock: bytes) -> None:
        raise NotImplementedError

    def storage(self) -> Path:
        return inside_home(user_data_path("setup-system") / "toolchains" / self.plugin_id)

    def commands(self, settings: PluginSettings) -> dict[str, str]:
        return self.binaries

    def links(self, generation: Path, settings: PluginSettings) -> dict[Path, bytes]:
        return {
            inside_home(
                Path.home() / ".local/bin" / name
            ): f'#!/bin/sh\nexec {shlex.quote(str(generation / binary))} "$@"\n'.encode()
            for name, binary in self.commands(settings).items()
        }

    def status(self, settings: PluginSettings) -> Plan:
        names = self.commands(settings)
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
            if self.readback(folder, settings) and all(
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

    def readback(self, folder: Path, settings: PluginSettings) -> bool:
        return all(
            (folder / binary).is_file()
            and os.access(folder / binary, os.X_OK)
            and (folder / binary).resolve().is_relative_to(folder)
            for binary in self.commands(settings).values()
        )

    def _plan(self, lock: bytes, settings: PluginSettings) -> Plan:
        digest = hashlib.sha256(lock).hexdigest()
        root = self.storage()
        checked_directory(root)
        folder = root / digest
        owned = {r.identity: r for r in owned_resources() if r.ownership == "created"}
        exists = folder.exists() or folder.is_symlink()
        if exists:
            checked_directory(folder)
            if str(folder) not in owned or not self.readback(folder, settings):
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
                ownership="created",
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
                    ownership="created",
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
                if not path.exists() and not path.is_symlink():
                    continue
                checked_directory(path)
                seal = path / "generation.sha256"
                if (
                    path.name != item.after
                    or seal.is_symlink()
                    or not seal.is_file()
                    or seal.stat().st_size != 64
                    or fingerprint(path) != seal.read_text()
                ):
                    raise ValueError("A tool generation changed after publication")
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
        for row in plan.resources:
            path = Path(row.identity)
            holding = path.with_name(".setup-system-retire-" + uuid.uuid4().hex)
            move_new(path, holding)
            try:
                if row.kind == "file":
                    observed = observe(holding)
                    if (
                        observed is None
                        or observed.sha256 != row.before
                        or observed.mode != row.permissions
                    ):
                        raise ValueError("Command file changed during cleanup")
                    holding.unlink()
                else:
                    with generation_lock(holding, exclusive=True):
                        if fingerprint(holding) != (holding / "generation.sha256").read_text():
                            raise ValueError("Tool generation changed during cleanup")
                        shutil.rmtree(holding)
            except (OSError, ValueError):
                if holding.exists():
                    move_new(holding, path)
                return Result(
                    outcome=Outcome.PARTIAL,
                    message="Inspect changed tool resources before resuming",
                )
        return Result(
            outcome=Outcome.CHANGED, message="Owned tools removed", resources=plan.resources
        )

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id == "check":
            return self.status(settings)
        if action_id == "remove-tools":
            return self.remove_plan()
        if action_id != "configure":
            return super().prepare(action_id, settings, inputs)
        with tempfile.TemporaryDirectory() as stage:
            return self._plan(self.lock(Path(stage), settings), settings)

    def mutate(
        self, action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan
    ) -> Result:
        if action_id == "remove-tools":
            return self.remove(plan)
        if action_id != "configure":
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
                    os.mkdir(folder.name, mode=0o700, dir_fd=directory)
                try:
                    self.install_tools(stage, folder, lock)
                    if not self.readback(folder, settings):
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
                lambda: all(not os.path.lexists(r.identity) for r in plan.resources)
            )
            return Result(
                outcome=Outcome.COMPLETE if good else Outcome.PARTIAL,
                message="Removal readback complete" if good else "A target still exists",
                resources=plan.resources,
            )
        if not plan.resources or plan.resources[0].kind != "toolchain_generation":
            return await super().verify(plan, settings, inputs)

        def matches() -> bool:
            first, *links = plan.resources
            if not self.readback(Path(first.identity), settings):
                return False
            for row in links:
                current = observe(Path(row.identity))
                if not current or current.sha256 != row.after or current.mode != row.permissions:
                    return False
            return True

        good = await asyncio.to_thread(matches)
        return Result(
            outcome=Outcome.COMPLETE if good else Outcome.PARTIAL,
            resources=plan.resources,
            message="Toolchain readback complete"
            if good
            else "The installed tools differ from the confirmed plan",
        )
