"""Discover plugin metadata and activate reviewed wheels in private uv environments."""

from __future__ import annotations

import asyncio
import configparser
import fcntl
import hashlib
import io
import os
import re
import shutil
import signal
import stat
import struct
import sys
import tempfile
import tomllib
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from email.parser import BytesParser
from importlib.metadata import distribution, distributions
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self
from urllib.parse import urljoin, urlsplit
from zipfile import BadZipFile, ZipFile

import httpx
from packaging.requirements import Requirement
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from setup_system.infrastructure.downloads import DownloadError, validate_https_url
from setup_system.layout import INSTALLED, verify_installation
from setup_system_sdk import PLUGIN_GROUP, UI_GROUP, Identifier, Inputs, Manifest

WHEEL_BYTES = 128 * 1024 * 1024
RELEASE_BYTES = 4 * 1024 * 1024
EXPANDED_BYTES = 256 * 1024 * 1024
MAX_FILES = 8192
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TrustPolicy(Inputs):
    """Set administrator-approved HTTPS origins and independently verified release digests."""

    hosts: frozenset[str] = frozenset()
    digests: frozenset[Digest] = frozenset()


class Wheel(Inputs):
    name: str = Field(max_length=255)
    sha256: Digest
    url: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def valid_filename(self) -> Self:
        if not re.fullmatch(r"[A-Za-z0-9._+-]+\.whl", self.name):
            raise ValueError("Expected a local wheel basename")
        parse_wheel_filename(self.name)
        return self


class ReleaseRecord(Inputs):
    manifest: Manifest
    wheels: tuple[Wheel, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def unique_wheels(self) -> Self:
        names = [parse_wheel_filename(w.name)[0] for w in self.wheels]
        if parse_wheel_filename(self.wheels[0].name)[1] != Version(self.manifest.version):
            raise ValueError("Primary wheel version differs from the release")
        if len(names) != len(set(names)):
            raise ValueError("A release repeats a wheel distribution")
        return self


class ReleaseDocument(Inputs):
    format: Literal[1] = 1
    releases: list[ReleaseRecord] = Field(min_length=1, max_length=128)


@dataclass(frozen=True)
class Release:
    """Keep immutable preview bytes and bind consent to their SHA-256 digest."""

    payload: bytes
    origin: str | None = None
    direct_name: str | None = None

    @property
    def digest(self) -> str:
        return _digest(self.payload)

    def selected(self) -> ReleaseRecord:
        if self.direct_name:
            if not parse_wheel_filename(self.direct_name)[3].intersection(sys_tags()):
                raise ValueError("No compatible plugin wheel for this interpreter")
            manifest = wheel_manifest(self.direct_name, self.payload)
            return ReleaseRecord(
                manifest=manifest, wheels=(Wheel(name=self.direct_name, sha256=self.digest),)
            )
        # Validate one version at a time so older supported SDKs can select a compatible release.
        envelope = _Envelope.model_validate_json(self.payload)
        records = []
        seen: set[tuple[str, Version]] = set()
        ids: set[str] = set()
        tags = frozenset(sys_tags())
        for raw in envelope.releases:
            identity = _ReleaseIdentity.model_validate(raw)
            ids.add(identity.manifest.id)
            try:
                record = ReleaseRecord.model_validate(raw)
            except (ValidationError, ValueError):
                continue
            key = (record.manifest.id, Version(record.manifest.version))
            if key in seen:
                raise ValueError("Duplicate plugin release identity")
            seen.add(key)
            if all(parse_wheel_filename(w.name)[3] & tags for w in record.wheels):
                records.append(record)
        if len(ids) != 1 or not records:
            raise ValueError("No compatible unambiguous plugin release")
        return max(records, key=lambda item: Version(item.manifest.version))

    @property
    def manifest(self) -> Manifest:
        return self.selected().manifest


class _ManifestId(BaseModel):
    id: Identifier


class _ReleaseIdentity(BaseModel):
    manifest: _ManifestId


class _Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal[1]
    releases: list[dict[str, JsonValue]] = Field(min_length=1, max_length=128)


@dataclass(frozen=True)
class CatalogEntry:
    manifest: Manifest | None
    reason: str = ""
    release: Release | None = None
    source: Path | None = None
    disabled: bool = False


def _members(archive: ZipFile) -> set[str]:
    entries = archive.infolist()
    if len(entries) > MAX_FILES or sum(x.file_size for x in entries) > EXPANDED_BYTES:
        raise ValueError("Wheel exceeds expansion limits")
    names: set[str] = set()
    dirs: set[str] = set()
    for item in entries:
        name = item.filename.rstrip("/")
        path = PurePosixPath(name)
        mode = stat.S_IFMT(item.external_attr >> 16)
        if (
            not name
            or not path.parts
            or path.is_absolute()
            or ".." in path.parts
            or str(path) != name
            or any(ord(c) < 32 or c in "\\:" for c in name)
            or name != item.orig_filename.rstrip("/")
            or item.flag_bits & 1
            or mode not in {0, stat.S_IFDIR, stat.S_IFREG}
            or (mode == stat.S_IFDIR and not item.is_dir())
        ):
            raise ValueError("Unsafe wheel member")
        if name in names or (name in dirs and not item.is_dir()):
            raise ValueError("Duplicate or overlapping wheel member")
        for parent in path.parents:
            if str(parent) in names and str(parent) not in dirs:
                raise ValueError("Wheel member overlaps a file")
            dirs.add(str(parent))
        names.add(name)
        if item.is_dir():
            dirs.add(name)
    return names - dirs


def _wheel_data(name: str, payload: bytes) -> tuple[set[str], dict[str, bytes], int, int]:
    distribution, version, _, _ = parse_wheel_filename(name)
    if len(payload) > WHEEL_BYTES:
        raise ValueError("Wheel exceeds size limit")
    end = payload.rfind(b"PK\x05\x06", max(0, len(payload) - 65557))
    if end < 0 or len(payload) - end < 22:
        raise ValueError("Missing wheel directory")
    _, disk, first_disk, disk_entries, entries, directory_size, offset, comment = (
        struct.unpack_from("<4sHHHHIIH", payload, end)
    )
    if (
        disk
        or first_disk
        or disk_entries != entries
        or entries > MAX_FILES
        or directory_size > 4 * 1024 * 1024
        or offset + directory_size != end
        or end + 22 + comment != len(payload)
    ):
        raise ValueError("Wheel directory exceeds the supported bounds")
    try:
        with ZipFile(io.BytesIO(payload)) as archive:
            files = _members(archive)
            metadata = [p for p in files if re.fullmatch(r"[^/]+\.dist-info/METADATA", p)]
            if len(metadata) != 1:
                raise ValueError("Expected one wheel distribution")
            prefix = metadata[0].removesuffix("/METADATA")
            if prefix != name.split("-")[0] + "-" + name.split("-")[1] + ".dist-info":
                raise ValueError("Wheel metadata directory disagrees with filename")
            if not {f"{prefix}/WHEEL", f"{prefix}/RECORD"} <= files:
                raise ValueError("Incomplete wheel")
            required = {metadata[0], f"{prefix}/entry_points.txt"}
            required.update(p for p in files if re.fullmatch(r"[A-Za-z_]\w*/manifest\.json", p))
            data = {}
            for p in required & files:
                if archive.getinfo(p).file_size > RELEASE_BYTES:
                    raise ValueError("Wheel metadata exceeds size limit")
                data[p] = archive.read(p)
            headers = BytesParser().parsebytes(data[metadata[0]], headersonly=True)
            if (
                canonicalize_name(headers.get("Name", "")) != distribution
                or Version(headers.get("Version", "")) != version
            ):
                raise ValueError("Wheel metadata identity mismatch")
            if any(Requirement(r).url for r in headers.get_all("Requires-Dist", [])):
                raise ValueError("Dependencies must resolve exclusively from reviewed wheels")
            for f in files:
                if (
                    f.endswith(".pth")
                    or f in {"sitecustomize.py", "usercustomize.py"}
                    or any(part.endswith(".data") for part in PurePosixPath(f).parts[:1])
                ):
                    raise ValueError(
                        "Wheel uses an unsupported installation hook or relocated data"
                    )
            return (
                files,
                data,
                len(archive.infolist()),
                sum(e.file_size for e in archive.infolist()),
            )
    except (BadZipFile, NotImplementedError, RuntimeError) as exc:
        raise ValueError("Invalid wheel archive") from exc


class _EntryPoints(configparser.ConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return optionstr


def _entrypoints(data: bytes) -> _EntryPoints:
    entries = _EntryPoints(interpolation=None)
    try:
        entries.read_string(data.decode())
    except (UnicodeError, configparser.Error) as error:
        raise ValueError("Invalid wheel entry point metadata") from error
    return entries


def wheel_manifest(filename: str, payload: bytes) -> Manifest:
    """Inspect the wheel as data, including entry-point and distribution agreement."""
    files, data, _, _ = _wheel_data(filename, payload)
    manifests = [p for p in data if p.endswith("/manifest.json")]
    entry_files = [p for p in data if p.endswith("/entry_points.txt")]
    if len(manifests) != 1 or len(entry_files) != 1:
        raise ValueError("Plugin wheel lacks unambiguous metadata")
    manifest = Manifest.model_validate_json(data[manifests[0]])
    module = manifest.entry_point.partition(":")[0]
    if (
        manifests[0] != f"{module}/manifest.json"
        or f"{module}/__init__.py" not in files
        or Version(manifest.version) != parse_wheel_filename(filename)[1]
    ):
        raise ValueError("Manifest module or version disagrees with wheel")
    entries = _entrypoints(data[entry_files[0]])
    if not entries.has_section(PLUGIN_GROUP) or dict(entries[PLUGIN_GROUP]) != {
        manifest.id: manifest.entry_point
    }:
        raise ValueError("Wheel entry point disagrees with manifest")
    if entries.has_section(UI_GROUP):
        for name, entry in entries.items(UI_GROUP):
            if name != manifest.id or not re.fullmatch(
                rf"{re.escape(module)}(?:\.\w+)*:\w+", entry
            ):
                raise ValueError("Invalid plugin UI entry point")
    return manifest


async def _fetch(url: str, policy: TrustPolicy, limit: int) -> bytes:
    # A fresh cookie jar for each hop prevents redirect credential forwarding.
    async with asyncio.timeout(60):
        for _ in range(6):
            validate_https_url(url, policy.hosts)
            async with httpx.AsyncClient(
                trust_env=False, follow_redirects=False, timeout=15
            ) as client:
                async with client.stream(
                    "GET", url, headers={"Accept-Encoding": "identity"}
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("Location")
                        if not location:
                            break
                        url = urljoin(url, location)
                        continue
                    if response.status_code != 200:
                        raise ValueError("Plugin source returned an unsuccessful response")
                    output = bytearray()
                    async for block in response.aiter_raw(chunk_size=65536):
                        if len(output) + len(block) > limit:
                            raise ValueError("Plugin response exceeds the size limit")
                        output.extend(block)
                    return bytes(output)
    raise ValueError("Plugin source redirected too many times")


async def preview_link(url: str, policy: TrustPolicy) -> Release:
    """Read an HTTPS release or wheel without loading or installing Python code."""
    name = PurePosixPath(urlsplit(url).path).name
    direct = name if name.endswith(".whl") else None
    try:
        data = await _fetch(url, policy, WHEEL_BYTES if direct else RELEASE_BYTES)
        release = Release(data, url, direct)
        await asyncio.to_thread(release.selected)
        return release
    except (httpx.HTTPError, TimeoutError, OSError, DownloadError):
        raise ValueError("Could not read the plugin from the approved source") from None


def _catalog_wheel(path: Path) -> CatalogEntry:
    if path.is_symlink() or path.stat().st_size > WHEEL_BYTES:
        raise ValueError("Unsafe packaged wheel")
    release = Release(path.read_bytes(), direct_name=path.name)
    manifest = release.manifest
    document = path.parent / (manifest.id + ".json")
    if document.exists():
        snapshot = Release(_local_file(document, RELEASE_BYTES))
        record = snapshot.selected()
        if (
            record.manifest != manifest
            or record.wheels[0].name != path.name
            or (record.wheels[0].sha256 != release.digest)
        ):
            raise ValueError("Packaged release identity differs from the wheel")
        release = snapshot
    return CatalogEntry(manifest, release=release, source=path)


def builtins(root: Path) -> tuple[CatalogEntry, ...]:
    """Read Debian/wheel catalogue or repository metadata without adding to the host import path."""
    entries = []
    catalog = root / "plugin_catalog"
    if not catalog.is_dir() and (root / "plugins").is_dir():
        catalog = root / "coverage" / "plugin_catalog"
    packaged = catalog.is_dir()
    paths = sorted(
        catalog.glob("*.whl") if packaged else (root / "plugins").glob("*/*/pyproject.toml")
    )
    for path in paths:
        try:
            if packaged:
                entry = _catalog_wheel(path)
            else:
                metadata = tomllib.loads(path.read_text())["project"]
                registry = metadata["entry-points"][PLUGIN_GROUP]
                if len(registry) != 1:
                    raise ValueError("Expected one plugin implementation")
                key, value = next(iter(registry.items()))
                module = value.partition(":")[0]
                if not re.fullmatch(r"[a-z][a-z0-9_]*", module):
                    raise ValueError("Invalid plugin module")
                candidates = [
                    folder / module / "manifest.json"
                    for folder in (path.parent, path.parent / "src")
                ]
                manifest = Manifest.model_validate_json(
                    next(p for p in candidates if p.is_file()).read_bytes()
                )
                if (
                    manifest.id != key
                    or manifest.entry_point != value
                    or manifest.version != metadata["version"]
                ):
                    raise ValueError("Source metadata disagrees with manifest")
                entry = CatalogEntry(manifest, source=path)
        except (OSError, ValueError, KeyError, TypeError, StopIteration, configparser.Error):
            entry = CatalogEntry(
                None, "Invalid, incompatible or unavailable plugin metadata", source=path
            )
        entries.append(entry)
    counts = Counter(e.manifest.id for e in entries if e.manifest)
    return tuple(
        replace(e, reason="Duplicate plugin identity", release=None)
        if e.manifest and counts[e.manifest.id] > 1
        else e
        for e in entries
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


def _local_file(path: Path, limit: int) -> bytes:
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        details = os.fstat(stream.fileno())
        if not stat.S_ISREG(details.st_mode) or details.st_size > limit:
            raise ValueError("Expected a bounded regular file")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("File exceeds the size limit")
        return data


def _private(path: Path, *, create: bool = False) -> None:
    if not path.is_absolute() or ".." in path.parts or path.resolve() != path:
        raise ValueError("Plugin storage requires a real absolute directory")
    if create:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError("Unsafe plugin storage directory")


def _directory(env_root: Path, plugin_id: str) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", plugin_id):
        raise ValueError("Invalid plugin identity")
    return env_root / plugin_id


def _read_generation(root: Path, pointer: _Pointer) -> Activation:
    generation = root / pointer.generation
    _private(generation)
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
    resources = locations[0] / metadata.entry_point.partition(":")[0]
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
    if any(os.path.lexists(root / name) for name in ("disabled.json", ".retiring.json")):
        return None
    return _state(env_root, plugin_id, disabled=False)


def installed(env_root: Path) -> tuple[CatalogEntry, ...]:
    """Report active environments independently, retaining errors for broken workers."""
    if not env_root.exists():
        return ()
    _private(env_root)
    result = []
    for path in sorted(env_root.iterdir()):
        try:
            if os.path.lexists(path / ".retiring.json"):
                from setup_system.extension_lifecycle import retirement_manifest

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


async def _run(argv: list[str], cwd: Path) -> None:
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(cwd),
            "LC_ALL": "C.UTF-8",
            "TMPDIR": str(cwd),
            "UV_NO_CONFIG": "1",
            "UV_NO_PROGRESS": "1",
        },
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        async with asyncio.timeout(120):
            if await process.wait():
                raise ValueError("Could not prepare or verify the isolated plugin environment")
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()


def _verify_layout(
    wheels: list[tuple[Wheel, bytes]], directory: Path, selected: ReleaseRecord
) -> None:
    occupied: set[str] = set()
    directories: set[str] = set()
    commands: set[str] = set()
    total_entries = 0
    expanded_bytes = 0
    for index, (wheel, content) in enumerate(wheels):
        if _digest(content) != wheel.sha256:
            raise ValueError("Downloaded wheel digest differs from the confirmed release")
        if index == 0 and wheel_manifest(wheel.name, content) != selected.manifest:
            raise ValueError("Plugin identity differs from the confirmed manifest")
        files, meta, count, expanded = _wheel_data(wheel.name, content)
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
        if current != prior or any(
            os.path.lexists(root / name) for name in ("disabled.json", ".retiring.json")
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


def verify_debian_dependencies(names: list[str]) -> None:
    """Check already-installed requirements in a venv with an authenticated Debian base."""
    local = Path(sys.prefix).resolve()
    packaged = Path("/usr/lib/python3/dist-packages")
    pending = [(name, "") for name in names]
    checked = set()
    while pending:
        name, extra = pending.pop()
        key = (canonicalize_name(name), extra)
        if key in checked:
            continue
        checked.add(key)
        item = distribution(name)
        origin = Path(str(item.locate_file(""))).resolve()
        if not origin.is_relative_to(local) and origin != packaged:
            raise ValueError("Dependency came from an unapproved Python installation")
        for raw in item.requires or ():
            requirement = Requirement(raw)
            if requirement.marker and not requirement.marker.evaluate({"extra": extra}):
                continue
            resolved = distribution(requirement.name)
            if requirement.url or Version(resolved.version) not in requirement.specifier:
                raise ValueError("Installed dependency differs from the reviewed requirement")
            pending.extend((requirement.name, e) for e in {"", *requirement.extras})


async def install_release(
    release: Release,
    *,
    policy: TrustPolicy,
    env_root: Path,
    wheelhouse: Path | None = None,
    validate_settings: Callable[[Activation], None] | None = None,
) -> Activation:
    """Activate a complete pinned closure after metadata, dependency and backend readback."""
    from setup_system.extension_lifecycle import seal_generation

    if await asyncio.to_thread(lambda: release.digest) not in policy.digests:
        raise ValueError("Explicit trust in an independently verified release digest is required")
    selected = await asyncio.to_thread(release.selected)
    root = _directory(env_root, selected.manifest.id)
    await asyncio.to_thread(_private, env_root, create=True)
    await asyncio.to_thread(_private, root, create=True)
    if await asyncio.to_thread(os.path.lexists, root / ".retiring.json"):
        raise ValueError("Finish the interrupted extension removal before updating")
    if await asyncio.to_thread(os.path.lexists, root / "disabled.json"):
        raise ValueError("Enable the disabled extension before updating it")
    try:
        prior = await asyncio.to_thread(_local_file, root / "active.json", 4096)
    except FileNotFoundError:
        prior = None
    if prior:
        previous = await asyncio.to_thread(
            _read_generation, root, _Pointer.model_validate_json(prior)
        )
        if previous.digest == release.digest:
            return previous
        if Version(selected.manifest.version) < Version(previous.manifest.version):
            raise ValueError("A plugin update cannot downgrade the active release")
        if (
            previous.manifest.settings_version != selected.manifest.settings_version
            or previous.manifest.settings_schema != selected.manifest.settings_schema
        ) and not validate_settings:
            raise ValueError("Settings migration or validation is required before activation")
    uv = shutil.which("uv") or shutil.which(
        "uv", path=os.pathsep.join((str(Path.home() / ".local/bin"), "/usr/local/bin"))
    )
    if not uv:
        raise ValueError("The uv environment manager is unavailable")
    system = Path(sys.executable) == Path("/usr/bin/python3") and Path(
        __file__
    ).resolve().is_relative_to(Path("/usr/lib/python3/dist-packages"))
    if system:
        await asyncio.to_thread(verify_installation, INSTALLED)
    generation = root / uuid.uuid4().hex
    generation.mkdir(mode=0o700)
    pointer = _Pointer(
        generation=generation.name, digest=release.digest, plugin_id=selected.manifest.id
    )
    committed = False
    try:
        with tempfile.TemporaryDirectory(dir=generation) as staging:
            stage = Path(staging)
            contents = []
            total = 0
            for i, wheel in enumerate(selected.wheels):
                if total >= EXPANDED_BYTES:
                    raise ValueError("Wheel closure exceeds aggregate limit")
                if i == 0 and release.direct_name:
                    content = release.payload
                elif wheelhouse is not None:
                    content = await asyncio.to_thread(
                        _local_file, _wheel_path(wheelhouse, wheel), WHEEL_BYTES
                    )
                elif release.origin:
                    content = await _fetch(
                        urljoin(release.origin, wheel.url or wheel.name), policy, WHEEL_BYTES
                    )
                else:
                    raise ValueError("A verified wheel is unavailable offline")
                total += len(content)
                if total > EXPANDED_BYTES:
                    raise ValueError("Wheel closure exceeds aggregate limit")
                contents.append((wheel, content))
            validation = asyncio.create_task(
                asyncio.to_thread(_verify_layout, contents, stage, selected)
            )
            try:
                await asyncio.shield(validation)
            except asyncio.CancelledError:
                await validation
                raise
            venv = generation / "venv"
            python = venv / "bin/python"
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
            if system:
                args.append("--system-site-packages")
            await _run(args, stage)
            await _run(
                [
                    uv,
                    "--offline",
                    "--no-config",
                    "--no-cache",
                    "pip",
                    "install",
                    "--no-index",
                    *(["--no-deps"] if system else []),
                    "--no-build",
                    "--no-python-downloads",
                    "--link-mode",
                    "copy",
                    "--python",
                    str(python),
                    *[str(stage / w.name) for w in selected.wheels],
                ],
                stage,
            )
            if system:
                # uv does not resolve against Debian packages inherited through site-packages.
                await _run(
                    [
                        str(python),
                        "-I",
                        "-B",
                        "-c",
                        "import sys; "
                        "from setup_system.extensions import verify_debian_dependencies; "
                        "verify_debian_dependencies(sys.argv[1:])",
                        *(str(parse_wheel_filename(w.name)[0]) for w in selected.wheels),
                    ],
                    stage,
                )
            (generation / "manifest.json").write_text(selected.manifest.model_dump_json())
            state = await asyncio.to_thread(_read_generation, root, pointer)
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
            )
            if validate_settings:
                await asyncio.to_thread(validate_settings, state)
        sealing = asyncio.create_task(asyncio.to_thread(seal_generation, generation))
        try:
            await asyncio.shield(sealing)
        except asyncio.CancelledError:
            await sealing
            raise
        publication = asyncio.create_task(asyncio.to_thread(_activate, root, prior, pointer))
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
            await asyncio.to_thread(shutil.rmtree, generation)
