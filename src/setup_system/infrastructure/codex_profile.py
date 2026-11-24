"""Merge the reviewed instructions into the current user's effective Codex profile."""

import hashlib
import os
import stat
import tomllib
from pathlib import Path
from typing import Annotated, Literal

import tomlkit
from pydantic import Field

from setup_system.domain.contracts import ContractModel
from setup_system.domain.managed_text import managed_block
from setup_system.domain.receipts import FileState
from setup_system.infrastructure.tool_paths import personal_path


class PolicySource(ContractModel):
    """Identity of the engineering instructions bundled with a release."""

    repository: Literal["serrja/baseRules"]
    document: Literal["docs/engineering.md"]
    commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def codex_settings(original: bytes) -> bytes:
    preferences = tomlkit.parse(original.decode())
    if "default_permissions" not in preferences and "permissions" not in preferences:
        preferences.setdefault("sandbox_mode", "workspace-write")
    preferences.setdefault("approval_policy", "on-request")
    return tomlkit.dumps(preferences).encode()


def original(path: Path) -> tuple[bytes, FileState | None]:
    personal_path(Path.home(), path, regular=True)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return b"", None
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_mode & 0o7022
            or info.st_uid != os.getuid()
            or info.st_size > 2**20
        ):
            raise ValueError("Review the ownership, mode and size of the Codex profile")
        data = stream.read(2**20 + 1)
        if len(data) != info.st_size or os.fstat(descriptor).st_mtime_ns != info.st_mtime_ns:
            raise ValueError("Codex profile changed during preview")
    return data, FileState(
        sha256=hashlib.sha256(data).hexdigest(),
        uid=info.st_uid,
        gid=info.st_gid,
        mode=stat.S_IMODE(info.st_mode),
    )


def files(config: Path) -> tuple[dict[Path, bytes], dict[Path, FileState | None]]:
    """Return desired bytes and precise preconditions for the two user-owned merges."""
    home = Path.home()
    directory = Path(os.environ.get("CODEX_HOME") or home / ".codex")
    if directory == home:
        raise ValueError("Codex configuration requires its own directory inside HOME")
    settings = directory / "config.toml"
    prior, settings_before = original(settings)
    instructions = directory / "AGENTS.override.md"
    previous, notes_before = original(instructions)
    if not previous.strip():
        instructions = directory / "AGENTS.md"
        previous, notes_before = original(instructions)
    source = config / "workspace/codex"
    policy = (source / "engineering.md").read_bytes()
    lock = PolicySource.model_validate(tomllib.loads((config / "workspace/rules.toml").read_text()))
    if hashlib.sha256(policy).hexdigest() != lock.sha256:
        raise ValueError("The packaged instructions do not match the reviewed source")
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    archive = xdg / "setup-system/policy"
    personal_path(home, archive)
    url = f"https://github.com/{lock.repository}/blob/{lock.commit}/{lock.document}"
    body = policy.decode().rstrip() + f"\n\n[Reviewed engineering instructions]({url})"
    return (
        {
            settings: codex_settings(prior),
            instructions: managed_block(previous.decode(), body).encode(),
            archive / "engineering.md": policy,
            archive / "LICENSE": (source / "LICENSE").read_bytes(),
        },
        {settings: settings_before, instructions: notes_before},
    )
