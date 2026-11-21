"""Prepare the root service's small Pydantic runtime from verified release bytes."""

from __future__ import annotations

import hashlib
import os
import platform
import stat
import sys
import tomllib
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NamedTuple
from urllib.parse import urlsplit

# Imported after the stdlib handoff authenticates the whole release. Keep the
# dependency closure finite, with all versions, filenames and hashes owned by uv.lock.
PACKAGES = (
    "annotated-types",
    "typing-extensions",
    "typing-inspection",
    "pydantic-core",
    "pydantic",
)
WHEEL_TAGS = {"amd64": "x86_64", "arm64": "aarch64"}
WHEEL_MAX = 8 * 1024 * 1024


class LockedWheel(NamedTuple):
    name: str
    url: str
    digest: str
    size: int


def locked_wheels(lock: Path, machine: str, python_version: str) -> tuple[LockedWheel, ...]:
    if machine not in WHEEL_TAGS.values():
        raise ValueError("Unsupported privileged runtime architecture")
    with lock.open("rb") as stream:
        packages = tomllib.load(stream)["package"]
    abi = "cp" + python_version.replace(".", "")
    wheels = []
    for name in PACKAGES:
        (record,) = (p for p in packages if p["name"] == name)
        if any(dep["name"] not in PACKAGES for dep in record.get("dependencies", ())):
            raise ValueError("Unreviewed privileged runtime dependency")
        tag = (
            f"{abi}-{abi}-manylinux_2_17_{machine}.manylinux2014_{machine}"
            if name == "pydantic-core"
            else "py3-none-any"
        )
        filename = f"{name.replace('-', '_')}-{record['version']}-{tag}.whl"
        (wheel,) = (w for w in record["wheels"] if urlsplit(w["url"]).path.endswith("/" + filename))
        algorithm, _, digest = wheel["hash"].partition(":")
        if (
            algorithm != "sha256"
            or type(wheel["size"]) is not int
            or len(digest) != 64
            or len(bytes.fromhex(digest)) != 32
            or not 0 < wheel["size"] <= WHEEL_MAX
        ):
            raise ValueError("Invalid privileged runtime wheel identity")
        wheels.append(LockedWheel(filename, wheel["url"], digest, wheel["size"]))
    return tuple(wheels)


def unpack(wheel: LockedWheel, archive: Path, directory: Path) -> None:
    with archive.open("rb") as stream:
        if (
            os.fstat(stream.fileno()).st_size != wheel.size
            or hashlib.file_digest(stream, "sha256").hexdigest() != wheel.digest
        ):
            raise ValueError("Privileged runtime wheel does not match the verified lock")
        stream.seek(0)
        with zipfile.ZipFile(stream) as package:
            members = package.infolist()
            if len(members) > 4096 or sum(m.file_size for m in members) > 32 * 1024 * 1024:
                raise ValueError("Oversized privileged runtime wheel")
            for member in members:
                name = member.filename.rstrip("/")
                parts = name.split("/")
                if (
                    any(
                        p in ("", ".", "..") or "\\" in p or any(ord(c) < 32 for c in p)
                        for p in parts
                    )
                    or stat.S_IFMT(member.external_attr >> 16)
                    not in (0, stat.S_IFREG, stat.S_IFDIR)
                    or parts[0].endswith(".data")
                    or name.endswith(".pth")
                ):
                    raise ValueError("Unsafe member in privileged runtime wheel")
                target = directory.joinpath(*parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output:
                    output.write(package.read(member))


def runtime_target(source: Path, machine: str) -> tuple[str, str]:
    with (source / "manifests/platforms.toml").open("rb") as stream:
        matrix = tomllib.load(stream)
    canonical = matrix["architectures"].get(machine, machine)
    if canonical not in matrix["hyprland"]["architectures"]:
        raise ValueError("Unsupported privileged runtime architecture")
    debian = matrix["debian"]["apt_architectures"][canonical]
    return matrix["hyprland"]["system_python"], WHEEL_TAGS[debian]


@contextmanager
def validation_runtime(source: Path) -> Iterator[None]:
    """Expose verified files only for the lifetime of this isolated root subprocess."""
    version, tag = runtime_target(source, platform.machine())
    if (
        sys.implementation.name != "cpython"
        or ".".join(map(str, sys.version_info[:2])) != version
        or not sys.flags.isolated
        or not sys.flags.no_site
        or not sys.flags.dont_write_bytecode
    ):
        raise ValueError(f"Privileged commands require isolated Debian CPython {version}")
    wheels = locked_wheels(source / "uv.lock", tag, version)
    with TemporaryDirectory(prefix=".contracts-", dir=source.parent) as temp:
        directory = Path(temp)
        for wheel in wheels:
            unpack(
                wheel,
                source / "src/setup_system/resources/privileged_wheels" / wheel.name,
                directory,
            )
        sys.path.insert(0, str(directory))
        try:
            yield
        finally:
            sys.path.remove(str(directory))
