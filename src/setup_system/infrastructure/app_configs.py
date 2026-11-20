"""Selected application adapters; preserve account stores and existing browser profiles."""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tomllib
from pathlib import Path

import tomlkit

from setup_system.downloads import GITHUB_RELEASE_HOSTS, DownloadPolicy, read_https
from setup_system.infrastructure.config_store import safe_file
from setup_system.infrastructure.theme import RESOURCE_ROOT
from setup_system.storage import atomic_write, new_backup


def configure_firefox() -> None:
    if os.geteuid() == 0:
        raise ValueError("Browser integration must run as the normal user")
    if not shutil.which("firefox-esr"):
        raise ValueError("Firefox ESR is not installed")
    # Profiles, saved logins, extension storage and bookmarks are never opened here.
    subprocess.run(
        ["xdg-settings", "set", "default-web-browser", "firefox-esr.desktop"],
        check=True,
        timeout=30,
    )
    observed = subprocess.run(
        ["xdg-settings", "get", "default-web-browser"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if observed.stdout.strip() != "firefox-esr.desktop":
        raise RuntimeError("Default browser readback failed")


def configure_codex(root: Path) -> None:
    if os.geteuid() == 0:
        raise ValueError("Codex must be installed as the normal user")
    document = tomllib.loads((RESOURCE_ROOT / "catalogs/codex.toml").read_text())
    architecture = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine())
    if architecture is None:
        raise ValueError("Unsupported Codex architecture")
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
    if target.exists() and target.read_bytes() != content:
        # Updating an existing binary is explicit and recoverable; no auth/config backup occurs.
        new_backup([target])
    atomic_write(target, content, 0o755, preserve_existing_mode=False)
    actual = subprocess.run(
        [str(target), "--version"], check=True, capture_output=True, text=True, timeout=15
    )
    if actual.stdout.strip() != "codex-cli " + document["version"]:
        raise ValueError("Codex version readback failed")
    config_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    if not config_home.is_relative_to(Path.home()):
        raise ValueError("Codex configuration must stay inside the verified HOME")
    config = config_home / "config.toml"
    safe_file(config, os.getuid())
    original = config.read_bytes() if config.exists() else b""
    options = tomlkit.parse(original.decode()) if original else tomlkit.document()
    # The pinned 0.170.0 schema still supports these settings. Preserve an existing model.
    options["sandbox_mode"] = "workspace-write"
    options["approval_policy"] = "on-request"
    if supports_high_reasoning(config_home, options.get("model")):
        options["model_reasoning_effort"] = "high"
    if "default_permissions" in options:
        # Existing explicit profile contracts need manual review; never weaken them silently.
        raise ValueError(
            "Review existing Codex permissions profiles before applying classic settings"
        )
    ensure_codex_policy(config_home)
    if original:
        new_backup([config])
    atomic_write(config, tomlkit.dumps(options).encode())
    # auth.json is deliberately never read, copied, backed up or deleted.


def ensure_codex_policy(config_home: Path) -> None:
    from setup_system.infrastructure.config_store import SettingsPaths
    from setup_system.infrastructure.user_files import apply_files

    revision = "8434509933feaa71ba312f8d02caad7362766d5a"
    url = f"https://raw.githubusercontent.com/serrja/baseRules/{revision}/docs/engineering.md"
    policy = read_https(url, DownloadPolicy(frozenset({"raw.githubusercontent.com"}), 65536, 30))
    if (
        hashlib.sha256(policy).hexdigest()
        != "f90f5476d874f68c976203fb201e13fe7399185c78ee9df87b483c1d4491f441"
    ):
        raise ValueError("Pinned baseRules policy checksum mismatch")
    agent = (
        "# Codex working contract\n\n"
        f"Read the [pinned engineering baseline]({url}) and the repository's AGENTS.md.\n"
        "Work from the exact Git root and preserve unrelated changes and secrets.\n"
        "Use workspace-write and on-request; keep authorization in native account tools.\n"
        "Instructions and policy text do not grant filesystem, network or system permissions.\n"
    ).encode()
    apply_files(
        {
            config_home / "AGENTS.md": agent,
            SettingsPaths().user.parent / "policy/engineering.md": policy,
        }
    )


def supports_high_reasoning(config_home: Path, model: object) -> bool:
    """Use public cached model capability metadata; never read the auth store."""
    if not isinstance(model, str):
        return False
    cache = config_home / "models_cache.json"
    safe_file(cache, os.getuid())
    if not cache.exists() or cache.stat().st_size > 5 * 1024 * 1024:
        return False
    data = json.loads(cache.read_text())
    models = data.get("models", []) if isinstance(data, dict) else []
    if not isinstance(models, list):
        return False
    for entry in models:
        if isinstance(entry, dict) and entry.get("slug", entry.get("id")) == model:
            levels = entry.get("supported_reasoning_levels", [])
            return isinstance(levels, list) and any(
                level == "high" or (isinstance(level, dict) and level.get("effort") == "high")
                for level in levels
            )
    return False
