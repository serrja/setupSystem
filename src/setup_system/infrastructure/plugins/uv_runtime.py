"""Prepare a verified private uv executable for an explicit extension activation."""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import platform
import shutil
import stat
import tarfile
import tempfile
from pathlib import Path

from platformdirs import user_cache_path
from setup_system_sdk.blocking import run_sync
from setup_system_sdk.downloads import (
    GITHUB_RELEASE_HOSTS,
    DownloadPolicy,
    download_https,
)
from setup_system_sdk.files.system_paths import protected

VERSION = "0.12.15"
ARCHIVES = {
    "x86_64": "f97935763c04be3e692460a7aaeaaab8fc3b78fcf8b389da820b38ae7423a638",
    "aarch64": "0e9a3499b0587d449c9ff684c0160da607826e4af1cee220bc87f378702d3e08",
}
LIMIT = 32 * 1024 * 1024


def _unpack(payload: bytes, name: str, digest: str, stage: Path) -> str:
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("The uv download differs from its trusted release digest")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        member = archive.getmember(f"{name}/uv")
        if not member.isfile() or not 0 < member.size <= 128 * 1024 * 1024:
            raise ValueError("Invalid uv executable in release archive")
        stream = archive.extractfile(member)
        if stream is None:
            raise ValueError("Missing uv executable")
        binary = stage / "uv"
        with stream, binary.open("xb") as target:
            shutil.copyfileobj(stream, target)
        binary.chmod(0o700)
        return str(binary)


async def executable(stage: Path) -> str:
    managed = os.environ.get("SETUP_SYSTEM_UV")
    if managed:
        binary = Path(managed)
        protected(binary, directory=False)
        receipt = binary.with_name("uv.sha256")
        protected(receipt, directory=False)
        with binary.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if receipt.read_text() != f"{actual}  uv\n":
            raise ValueError("The package-managed uv executable differs from its digest")
        return str(binary)
    arch = platform.machine()
    if platform.system() != "Linux" or arch not in ARCHIVES:
        raise ValueError("Install uv for this platform before activating an extension")
    name = f"uv-{arch}-unknown-linux-gnu"
    digest = ARCHIVES[arch]
    cache = user_cache_path("setup-system") / "uv"
    archive = cache / f".uv-{digest}.tar.gz"

    def cached() -> bytes:
        if not cache.is_absolute() or cache.resolve() != cache:
            raise ValueError("The uv cache must be a real absolute directory")
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = cache.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError("The uv cache must be owned by the current user")
        with os.fdopen(os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as fp:
            info = os.fstat(fp.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
                raise ValueError("Expected a bounded uv release archive")
            return fp.read(LIMIT + 1)

    downloaded = False
    try:
        payload = await run_sync(cached)
    except FileNotFoundError:
        url = f"https://github.com/astral-sh/uv/releases/download/{VERSION}/{name}.tar.gz"
        payload = await download_https(url, DownloadPolicy(GITHUB_RELEASE_HOSTS, LIMIT, 30, 180))
        downloaded = True

    def prepare() -> str:
        binary = _unpack(payload, name, digest, stage)
        if downloaded:
            with tempfile.NamedTemporaryFile(dir=cache, delete=False) as fp:
                temporary = Path(fp.name)
                try:
                    fp.write(payload)
                    fp.flush()
                    os.replace(temporary, archive)
                finally:
                    temporary.unlink(missing_ok=True)

        return binary

    pending = asyncio.create_task(run_sync(prepare))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        await pending
        raise
