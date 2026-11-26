"""Discover plugin metadata and activate reviewed wheels in private uv environments."""

from __future__ import annotations

import asyncio
import configparser
import fcntl
import os
import re
import shutil
import sys
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.parser import BytesParser
from enum import StrEnum
from importlib.metadata import distributions
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin

import anyio
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version
from pydantic import Field
from setup_system_sdk import PLUGIN_GROUP, UI_GROUP
from setup_system_sdk.blocking import run_sync
from setup_system_sdk.downloads import DownloadClient
from setup_system_sdk.files import retirement_checkpoint as checkpoint
from setup_system_sdk.files.generations import bounded_file as _local_file
from setup_system_sdk.files.generations import private_storage as _private
from setup_system_sdk.operations import Executor
from setup_system_sdk.processes import ProcessRunner, ProcessSpec
from setup_system_sdk.schemas.common import Identifier, Inputs
from setup_system_sdk.schemas.manifest import Manifest

from setup_system.infrastructure.plugins import uv_runtime
from setup_system.infrastructure.plugins.discovery import (
    EXPANDED_BYTES,
    MAX_FILES,
    RELEASE_BYTES,
    WHEEL_BYTES,
    CatalogEntry,
    Digest,
    Release,
    ReleaseRecord,
    TrustPolicy,
    Wheel,
    _digest,
    _entrypoints,
    _fetch,
    _wheel_data,
    wheel_manifest,
)


class _Pointer(Inputs):
    generation: str = Field(pattern=r"^[0-9a-f]{32}$")
    digest: Digest
    plugin_id: Identifier


@dataclass(frozen=True)
class Activation:
    python: Path
    manifest: Manifest
    digest: str
    resources: Path

    @property
    def executor(self) -> Executor:
        return Executor(
            plugin_id=self.manifest.id,
            version=self.manifest.version,
            release_digest=self.digest,
            generation=self.python.parents[2].name,
        )


def _directory(env_root: Path, plugin_id: str) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", plugin_id):
        raise ValueError("Invalid plugin identity")
    return env_root / plugin_id


def _read_generation(root: Path, pointer: _Pointer, *, staging: bool = False) -> Activation:
    from setup_system.infrastructure.plugins.lifecycle import verify_generation

    generation = root / pointer.generation
    _private(generation)
    if not staging:
        verify_generation(generation)
    metadata = Manifest.model_validate_json(
        _local_file(generation / "manifest.json", RELEASE_BYTES)
    )
    if metadata.id != pointer.plugin_id:
        raise ValueError("Stored plugin identity changed")
    locations = list((generation / "venv" / "lib").glob("python*/site-packages"))
    if len(locations) != 1:
        raise ValueError("Plugin worker environment is unavailable")
    entries = [
        ep
        for dist in distributions(path=[str(locations[0])])
        for ep in dist.entry_points
        if ep.group == PLUGIN_GROUP
    ]
    if (
        len(entries) != 1
        or entries[0].name != metadata.id
        or entries[0].value != metadata.entry_point
    ):
        raise ValueError("Installed plugin metadata changed")
    resources = locations[0] / metadata.entry_point.partition(":")[0].partition(".")[0]
    installed = Manifest.model_validate_json(
        _local_file(resources / "manifest.json", RELEASE_BYTES)
    )
    python = generation / "venv" / "bin" / "python"
    if installed != metadata or not python.is_file():
        raise ValueError("Plugin environment no longer matches the active receipt")
    return Activation(python, metadata, pointer.digest, resources)


def _state(env_root: Path, plugin_id: str, *, disabled: bool) -> Activation | None:
    root = _directory(env_root, plugin_id)
    if not root.exists():
        return None
    _private(env_root)
    _private(root)
    try:
        name = "disabled.json" if disabled else "active.json"
        pointer = _Pointer.model_validate_json(_local_file(root / name, 4096))
    except FileNotFoundError:
        return None
    if pointer.plugin_id != plugin_id:
        raise ValueError("Invalid active plugin receipt")
    return _read_generation(root, pointer)


def active(env_root: Path, plugin_id: str) -> Activation | None:
    """Inspect a specific venv's distribution metadata without loading its modules."""
    root = _directory(env_root, plugin_id)
    if checkpoint.pending(root) or any(
        os.path.lexists(root / name) for name in ("disabled.json", ".retiring.json")
    ):
        return None
    return _state(env_root, plugin_id, disabled=False)


def installed(env_root: Path) -> tuple[CatalogEntry, ...]:
    """Report active environments independently, retaining errors for broken workers."""
    if not env_root.exists():
        return ()
    _private(env_root)
    result = []
    paths = {p for p in env_root.iterdir() if p.name != checkpoint.NAMESPACE}
    try:
        paths.update(env_root / name for name in checkpoint.targets(env_root))
    except (OSError, ValueError):
        result.append(
            CatalogEntry(None, "Invalid retirement storage", source=env_root / checkpoint.NAMESPACE)
        )
    for path in sorted(paths):
        try:
            if checkpoint.pending(path) or os.path.lexists(path / ".retiring.json"):
                from setup_system.infrastructure.plugins.lifecycle import retirement_manifest

                result.append(CatalogEntry(retirement_manifest(path), source=path, disabled=True))
                continue
            disabled = os.path.lexists(path / "disabled.json")
            if disabled and os.path.lexists(path / "active.json"):
                raise ValueError("Extension has conflicting active and disabled receipts")
            state = _state(env_root, path.name, disabled=disabled)
            if state:
                result.append(
                    CatalogEntry(state.manifest, source=state.resources, disabled=disabled)
                )
        except (OSError, ValueError, TypeError, configparser.Error):
            result.append(CatalogEntry(None, "Unavailable plugin environment", source=path))
    return tuple(result)


class EnvironmentStep(StrEnum):
    CREATE = "creating the environment"
    INSTALL = "installing the plugin wheel"
    DEPENDENCIES = "checking runtime dependencies"
    BACKEND = "loading the plugin backend"


async def _run(argv: list[str], cwd: Path, step: EnvironmentStep) -> None:
    result = await ProcessRunner().run(
        ProcessSpec(
            tuple(argv),
            cwd=cwd,
            timeout=120,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(cwd),
                "LC_ALL": "C.UTF-8",
                "TMPDIR": str(cwd),
                "UV_NO_CONFIG": "1",
                "UV_NO_PROGRESS": "1",
            },
            discard_stdout=True,
            discard_stderr=True,
        )
    )
    if result.returncode:
        raise ValueError(f"Plugin activation failed while {step.value} (exit {result.returncode})")


def verify_dependencies(
    primary: str, metadata: dict[str, tuple[Version, tuple[Requirement, ...]]]
) -> None:
    pending = [(primary, frozenset[str]())]
    visited = set()
    while pending:
        name, extras = pending.pop()
        if (name, extras) in visited:
            continue
        visited.add((name, extras))
        for requirement in metadata[name][1]:
            if requirement.marker and not any(
                requirement.marker.evaluate({"extra": e}) for e in extras | {""}
            ):
                continue
            dependency = canonicalize_name(requirement.name)
            if (
                requirement.url
                or dependency not in metadata
                or metadata[dependency][0] not in requirement.specifier
            ):
                raise ValueError("The release lacks a compatible reviewed dependency")
            pending.append((dependency, frozenset(requirement.extras)))
    if {name for name, _ in visited} != set(metadata):
        raise ValueError("A wheel is outside the primary distribution's dependency closure")


def _verify_layout(
    wheels: list[tuple[Wheel, bytes]], directory: Path, selected: ReleaseRecord
) -> None:
    occupied: set[str] = set()
    directories: set[str] = set()
    commands: set[str] = set()
    metadata: dict[str, tuple[Version, tuple[Requirement, ...]]] = {}
    total_entries = 0
    expanded_bytes = 0
    for index, (wheel, content) in enumerate(wheels):
        if _digest(content) != wheel.sha256:
            raise ValueError("Downloaded wheel digest differs from the confirmed release")
        if index == 0 and wheel_manifest(wheel.name, content) != selected.manifest:
            raise ValueError("Plugin identity differs from the confirmed manifest")
        files, meta, count, expanded = _wheel_data(wheel.name, content)
        dist, version, _, _ = parse_wheel_filename(wheel.name)
        (raw,) = [v for path, v in meta.items() if path.endswith(".dist-info/METADATA")]
        headers = BytesParser().parsebytes(raw, headersonly=True)
        interpreter = ".".join(map(str, sys.version_info[:3]))
        if interpreter not in SpecifierSet(headers.get("Requires-Python", "")):
            raise ValueError("A wheel requires a different interpreter")
        metadata[dist] = (
            version,
            tuple(Requirement(r) for r in headers.get_all("Requires-Dist", [])),
        )
        total_entries += count
        expanded_bytes += expanded
        if total_entries > MAX_FILES or expanded_bytes > EXPANDED_BYTES:
            raise ValueError("Wheel closure exceeds aggregate expansion limits")
        for path, data in meta.items():
            if not path.endswith("/entry_points.txt"):
                continue
            entries = _entrypoints(data)
            if index and any(entries.has_section(group) for group in (PLUGIN_GROUP, UI_GROUP)):
                raise ValueError("Dependency cannot install a second plugin implementation")
            for group in ("console_scripts", "gui_scripts"):
                for command in entries.options(group) if entries.has_section(group) else ():
                    if (
                        not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._+-]*", command)
                        or re.fullmatch(r"(pythonw?|pip)([0-9]+([.][0-9]+)*)?", command)
                        or command.casefold().startswith("activate")
                        or command in commands
                    ):
                        raise ValueError("Wheel contains an unsafe or overlapping command")
                    commands.add(command)
        parents = {str(p) for f in files for p in PurePosixPath(f).parents}
        if files & (occupied | directories) or parents & occupied:
            raise ValueError("Wheels contain conflicting installation paths")
        occupied.update(files)
        directories.update(parents)
    verify_dependencies(parse_wheel_filename(wheels[0][0].name)[0], metadata)
    for wheel, content in wheels:
        (directory / wheel.name).write_bytes(content)


def _activate(root: Path, prior: bytes | None, pointer: _Pointer) -> None:
    fd = os.open(root, os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            current = _local_file(root / "active.json", 4096)
        except FileNotFoundError:
            current = None
        if (
            current != prior
            or checkpoint.pending(root)
            or any(os.path.lexists(root / name) for name in ("disabled.json", ".retiring.json"))
        ):
            raise ValueError("Plugin activation changed during staging")
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as staged:
            temporary = Path(staged.name)
            try:
                staged.write(pointer.model_dump_json().encode())
                staged.flush()
                os.fsync(staged.fileno())
                os.replace(temporary, root / "active.json")
                os.fsync(fd)
            finally:
                temporary.unlink(missing_ok=True)
    finally:
        os.close(fd)


def _wheel_path(wheelhouse: Path, wheel: Wheel) -> Path:
    if wheel.url == "runtime/" + wheel.name:
        return wheelhouse / "runtime" / wheel.name
    return wheelhouse / wheel.name


async def install_release(
    release: Release,
    *,
    policy: TrustPolicy,
    env_root: Path,
    wheelhouse: Path | None = None,
    validate_settings: Callable[[Activation], Awaitable[None]] | None = None,
    client: DownloadClient | None = None,
) -> Activation:
    """Activate a complete pinned closure after metadata, dependency and backend readback."""
    from setup_system.infrastructure.plugins.lifecycle import seal_generation

    if await run_sync(lambda: release.digest) not in policy.digests:
        raise ValueError("Explicit trust in an independently verified release digest is required")
    selected = await run_sync(release.selected)
    root = _directory(env_root, selected.manifest.id)
    await run_sync(_private, env_root, create=True)

    def create_plugin_root() -> None:
        with checkpoint.allow_creation(root):
            _private(root, create=True)

    await run_sync(create_plugin_root)
    if await run_sync(os.path.lexists, root / ".retiring.json"):
        raise ValueError("Finish the interrupted extension removal before updating")
    if await run_sync(os.path.lexists, root / "disabled.json"):
        raise ValueError("Enable the disabled extension before updating it")
    try:
        prior = await run_sync(_local_file, root / "active.json", 4096)
    except FileNotFoundError:
        prior = None
    if prior:
        previous = await run_sync(_read_generation, root, _Pointer.model_validate_json(prior))
        if previous.digest == release.digest:
            return previous
        if Version(selected.manifest.version) < Version(previous.manifest.version):
            raise ValueError("A plugin update cannot downgrade the active release")
        if (
            previous.manifest.settings_version != selected.manifest.settings_version
            or previous.manifest.settings_schema != selected.manifest.settings_schema
        ) and not validate_settings:
            raise ValueError("Settings migration or validation is required before activation")
    generation = root / uuid.uuid4().hex
    generation.mkdir(mode=0o700)
    pointer = _Pointer(
        generation=generation.name, digest=release.digest, plugin_id=selected.manifest.id
    )
    committed = False
    try:
        with tempfile.TemporaryDirectory(dir=generation) as staging:
            stage = Path(staging)
            fetched: dict[str, bytes] = {}
            total = 0
            limiter = anyio.CapacityLimiter(max(1, EXPANDED_BYTES // WHEEL_BYTES))

            async def fetch(index: int, wheel: Wheel) -> None:
                nonlocal total
                async with limiter:
                    if index == 0 and release.direct_name:
                        content = release.payload
                    elif wheelhouse is not None:
                        content = await run_sync(
                            _local_file, _wheel_path(wheelhouse, wheel), WHEEL_BYTES
                        )
                    elif release.origin:
                        content = await _fetch(
                            urljoin(release.origin, wheel.url or wheel.name),
                            policy,
                            WHEEL_BYTES,
                            client,
                        )
                    else:
                        raise ValueError("A verified wheel is unavailable offline")
                    total += len(content)
                    if total > EXPANDED_BYTES:
                        raise ValueError("Wheel closure exceeds aggregate limit")
                    fetched[wheel.name] = content

            async with anyio.create_task_group() as group:
                for index, wheel in enumerate(selected.wheels):
                    group.start_soon(fetch, index, wheel)
            contents = [(wheel, fetched.pop(wheel.name)) for wheel in selected.wheels]
            validation = asyncio.create_task(run_sync(_verify_layout, contents, stage, selected))
            try:
                await asyncio.shield(validation)
            except asyncio.CancelledError:
                await validation
                raise
            venv = generation / "venv"
            python = venv / "bin/python"
            uv = await uv_runtime.executable(stage)
            args = [
                uv,
                "--offline",
                "--no-config",
                "--no-cache",
                "venv",
                "--no-python-downloads",
                "--python",
                sys.executable,
                str(venv),
            ]
            await _run(args, stage, EnvironmentStep.CREATE)
            await _run(
                [
                    uv,
                    "--offline",
                    "--no-config",
                    "--no-cache",
                    "pip",
                    "install",
                    "--no-index",
                    "--find-links",
                    str(stage),
                    "--no-build",
                    "--no-python-downloads",
                    "--link-mode",
                    "copy",
                    "--python",
                    str(python),
                    *[str(stage / w.name) for w in selected.wheels],
                ],
                stage,
                EnvironmentStep.INSTALL,
            )
            (generation / "manifest.json").write_text(selected.manifest.model_dump_json())
            state = await run_sync(_read_generation, root, pointer, staging=True)
            await _run(
                [
                    str(state.python),
                    "-I",
                    "-B",
                    "-c",
                    "import sys; from setup_system_sdk.worker import load_backend; "
                    "load_backend(sys.argv[1])",
                    selected.manifest.id,
                ],
                stage,
                EnvironmentStep.BACKEND,
            )
            if validate_settings:
                await validate_settings(state)
        sealing = asyncio.create_task(run_sync(seal_generation, generation))
        try:
            await asyncio.shield(sealing)
        except asyncio.CancelledError:
            await sealing
            raise
        publication = asyncio.create_task(run_sync(_activate, root, prior, pointer))
        try:
            await asyncio.shield(publication)
        except asyncio.CancelledError:
            # Once pointer publication starts, report its verified outcome.
            await publication
        committed = True
        return state
    finally:
        if not committed:
            # The UUID directory was made by this call and has never been published.
            await run_sync(shutil.rmtree, generation)
