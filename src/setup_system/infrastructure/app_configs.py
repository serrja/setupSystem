"""Selected application adapters; preserve account stores and existing browser profiles."""

from __future__ import annotations

import hashlib
import io
import os
import platform
import subprocess
import tarfile
import tomllib
from pathlib import Path

import tomlkit

from setup_system.application.project_catalog import ProjectCatalog
from setup_system.capabilities import load_policy
from setup_system.downloads import GITHUB_RELEASE_HOSTS, DownloadPolicy, read_https
from setup_system.infrastructure.config_store import SettingsPaths, safe_file
from setup_system.infrastructure.theme import RESOURCE_ROOT
from setup_system.infrastructure.user_files import apply_files
from setup_system.storage import managed_block


def configure_firefox() -> None:
    if os.geteuid() == 0:
        raise ValueError("Browser integration must run as the normal user")
    result = subprocess.run(
        ["/usr/bin/firefox-esr", "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if "Firefox" not in result.stdout:
        raise ValueError("Firefox ESR version readback failed")


def codex_settings(original: bytes) -> bytes:
    options = tomlkit.parse(original.decode())
    if "default_permissions" not in options and "permissions" not in options:
        options.setdefault("sandbox_mode", "workspace-write")
    options.setdefault("approval_policy", "on-request")
    return tomlkit.dumps(options).encode()


def _original(path: Path) -> tuple[bytes, str | None]:
    safe_file(path, os.getuid())
    if not path.exists():
        return b"", None
    if path.stat().st_size > 1024 * 1024:
        raise ValueError(f"Review oversized configuration before applying: {path.name}")
    data = path.read_bytes()
    return data, hashlib.sha256(data).hexdigest()


def configure_codex(root: Path) -> None:
    if os.geteuid() == 0:
        raise ValueError("Codex must be installed as the normal user")
    document = tomllib.loads((RESOURCE_ROOT / "catalogs/codex.toml").read_text())
    architecture = load_policy(root).debian_architecture(platform.machine())
    config_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    if (
        not config_home.is_absolute()
        or not config_home.is_relative_to(Path.home())
        or ".." in config_home.parts
    ):
        raise ValueError("Codex configuration must stay inside the verified HOME")
    config = config_home / "config.toml"
    original, before = _original(config)
    merged = codex_settings(original)
    override = config_home / "AGENTS.override.md"
    override_data, override_hash = _original(override)
    instructions = override if override_data.strip() else config_home / "AGENTS.md"
    prior, instruction_hash = (
        (override_data, override_hash) if instructions == override else _original(instructions)
    )
    policy_path = SettingsPaths().user.parent / "policy/engineering.md"
    catalog = ProjectCatalog.model_validate(
        tomllib.loads((root / "manifests/projects.toml").read_text())
    )
    rules = next(item for item in catalog.project if item.id == "serrja/baseRules").policy
    if rules is None:
        raise ValueError("Missing baseRules provenance")
    url = f"https://raw.githubusercontent.com/serrja/baseRules/{rules.commit}/{rules.document}"
    policy = read_https(url, DownloadPolicy(frozenset({"raw.githubusercontent.com"}), 65536, 30))
    if hashlib.sha256(policy).hexdigest() != rules.sha256:
        raise ValueError("Pinned baseRules policy checksum mismatch")
    body = (
        f"Read the reviewed engineering contract at `{policy_path}` and the local "
        "repository instructions. Resolve the exact Git root and preserve unrelated files."
    )
    updated_instructions = managed_block(prior.decode(), body).encode()
    pin = document[architecture]
    data = read_https(pin["url"], DownloadPolicy(GITHUB_RELEASE_HOSTS, 256 * 1024 * 1024, 60))
    if hashlib.sha256(data).hexdigest() != pin["sha256"]:
        raise ValueError("Official Codex checksum mismatch")
    target = Path.home() / ".local/bin/codex"
    safe_file(target, os.getuid())
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        if (
            len(members) != 1
            or members[0].name != pin["member"]
            or not members[0].isfile()
            or members[0].size > 512 * 1024 * 1024
        ):
            raise ValueError("Unexpected official Codex archive contents")
        stream = archive.extractfile(members[0])
        if stream is None:
            raise ValueError("Missing Codex binary")
        content = stream.read()
    apply_files(
        {target: content, config: merged, instructions: updated_instructions, policy_path: policy},
        modes={target: 0o755, config: 0o600},
        expected={config: before, instructions: instruction_hash},
    )
    actual = subprocess.run(
        [str(target), "--version"], check=True, capture_output=True, text=True, timeout=15
    )
    if actual.stdout.strip() != "codex-cli " + document["version"]:
        raise ValueError("Codex version readback failed")
