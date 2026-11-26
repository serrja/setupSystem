"""Discover plugin metadata and activate reviewed wheels in private uv environments."""

from __future__ import annotations

import configparser
import hashlib
import io
import re
import stat
import struct
from collections import Counter
from dataclasses import dataclass, replace
from email.parser import BytesParser
from importlib.metadata import distributions
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
from zipfile import BadZipFile, ZipFile

import httpx
from packaging.requirements import Requirement
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator
from setup_system_sdk import PLUGIN_GROUP, UI_GROUP
from setup_system_sdk.blocking import run_sync
from setup_system_sdk.downloads import DownloadClient, DownloadError, DownloadPolicy, download_https
from setup_system_sdk.files.generations import bounded_file as _local_file
from setup_system_sdk.schemas.common import Identifier, Inputs
from setup_system_sdk.schemas.manifest import Manifest

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
            if all(parse_wheel_filename(w.name)[3] & tags for w in record.wheels):
                if key in seen:
                    raise ValueError("Ambiguous compatible plugin release")
                seen.add(key)
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
    backend_path = manifest.entry_point.partition(":")[0].replace(".", "/")
    module = backend_path.partition("/")[0]
    if (
        manifests[0] != f"{module}/manifest.json"
        or f"{module}/__init__.py" not in files
        or not ({backend_path + ".py", backend_path + "/__init__.py"} & files)
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


async def _fetch(
    url: str, policy: TrustPolicy, limit: int, client: DownloadClient | None = None
) -> bytes:
    bounds = DownloadPolicy(policy.hosts, limit, 15, 60)
    return await client.read(url, bounds) if client else await download_https(url, bounds)


async def preview_link(
    url: str, policy: TrustPolicy, client: DownloadClient | None = None
) -> Release:
    """Read an HTTPS release or wheel without loading or installing Python code."""
    name = PurePosixPath(urlsplit(url).path).name
    direct = name if name.endswith(".whl") else None
    try:
        data = await _fetch(url, policy, WHEEL_BYTES if direct else RELEASE_BYTES, client)
        release = Release(data, url, direct)
        await run_sync(release.selected)
        return release
    except (httpx.HTTPError, TimeoutError, OSError, DownloadError):
        raise ValueError("Could not read the plugin from the approved source") from None


def release_catalog(paths: tuple[Path, ...]) -> tuple[CatalogEntry, ...]:
    """Inspect explicitly configured release descriptors without loading code."""
    entries = []
    for directory in paths:
        if not directory.is_absolute():
            raise ValueError("Catalog directories must be absolute")
        for document in sorted(directory.glob("*.json")):
            try:
                release = Release(_local_file(document, RELEASE_BYTES))
                selected = release.selected()
                if document.stem != selected.manifest.id:
                    raise ValueError("Release filename differs from the declared plugin")
                entries.append(CatalogEntry(selected.manifest, source=document, release=release))
            except (OSError, ValueError):
                entries.append(CatalogEntry(None, "Invalid release descriptor", source=document))
    counts = Counter(e.manifest.id for e in entries if e.manifest)
    return tuple(
        replace(e, reason="Duplicate plugin release", release=None)
        if e.manifest and counts[e.manifest.id] > 1
        else e
        for e in entries
    )


def entry_point_catalog() -> tuple[CatalogEntry, ...]:
    """Discover installed manifests without importing the advertised implementation."""
    catalog = []
    for dist in distributions():
        for entry in dist.entry_points:
            if entry.group != PLUGIN_GROUP:
                continue
            try:
                module = entry.module.partition(".")[0]
                if not re.fullmatch(r"[a-z][a-z0-9_]*", module):
                    raise ValueError("Invalid distribution module")
                path = Path(str(dist.locate_file(f"{module}/manifest.json")))
                manifest = Manifest.model_validate_json(_local_file(path, RELEASE_BYTES))
                if (entry.name, entry.value, dist.version) != (
                    manifest.id,
                    manifest.entry_point,
                    manifest.version,
                ):
                    raise ValueError("Distribution metadata differs from manifest")
                catalog.append(CatalogEntry(manifest, source=path))
            except (ValueError, OSError):
                catalog.append(CatalogEntry(None, "Invalid installed manifest"))
    counts = Counter(row.manifest.id for row in catalog if row.manifest)
    return tuple(
        replace(row, reason="Duplicate plugin identity")
        if row.manifest and counts[row.manifest.id] > 1
        else row
        for row in catalog
    )
