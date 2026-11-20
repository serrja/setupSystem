"""Install only a selected, hash-locked language tool closure into a user environment."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path

from platformdirs import user_cache_path, user_data_path

from setup_system.downloads import DownloadPolicy, copy_https
from setup_system.infrastructure.config_store import safe_file
from setup_system.infrastructure.editor_downloads import expose_commands, tree_digest
from setup_system.infrastructure.file_receipts import checked_directory
from setup_system.infrastructure.theme import RESOURCE_ROOT
from setup_system.runtime import managed_uv
from setup_system.storage import atomic_write


def install_python_tools() -> None:
    if os.geteuid() == 0:
        raise ValueError("Language tools cannot be installed as root")
    from setup_system.planning import detect_platform

    capability = detect_platform()
    if not capability.implemented:
        raise ValueError(capability.reason)
    arch = capability.architecture
    uv = managed_uv(("debian", arch))
    requirements = RESOURCE_ROOT / "catalogs/python_tools.requirements.txt"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    parent = checked_directory(user_data_path("setup-system") / "language-tools")
    legacy = parent / "python"
    old_receipt = legacy / "setup-system.json"
    safe_file(old_receipt, os.getuid())
    if (
        old_receipt.exists()
        and json.loads(old_receipt.read_text()).get("requirements_sha256") == digest
    ):
        environment = checked_directory(legacy)
    else:
        environment = checked_directory(parent / ("python-" + digest))
    receipt = environment / "setup-system.json"
    safe_file(receipt, os.getuid())
    if environment.exists():
        if (
            not receipt.is_file()
            or json.loads(receipt.read_text()).get("requirements_sha256") != digest
        ):
            raise ValueError("Existing language environment requires explicit review")
    else:
        subprocess.run(
            [str(uv), "venv", "--python", sys.executable, str(environment)], check=True, timeout=120
        )
        atomic_write(
            receipt, json.dumps({"requirements_sha256": digest, "status": "prepared"}).encode()
        )
    subprocess.run(
        [
            str(uv),
            "pip",
            "sync",
            "--require-hashes",
            "--only-binary=:all:",
            "--python",
            str(environment / "bin/python"),
            str(requirements),
        ],
        check=True,
        timeout=900,
    )
    for package in ("ruff", "basedpyright"):
        result = subprocess.run(
            [str(environment / "bin" / package), "--version"],
            check=True,
            timeout=30,
            capture_output=True,
            text=True,
        )
        if not result.stdout.strip():
            raise RuntimeError("Installed language tool failed verification: " + package)
    commands = {
        name: environment / "bin" / name
        for name in ("ruff", "basedpyright", "basedpyright-langserver")
    }
    if not all(path.is_file() for path in commands.values()):
        raise RuntimeError("Hash-locked Python language command is missing")
    expose_commands(commands)
    atomic_write(
        receipt, json.dumps({"requirements_sha256": digest, "status": "complete"}).encode()
    )


def _npm_profile() -> dict[str, Path]:
    lock = (RESOURCE_ROOT / "catalogs/typescript_vue.lock.json").read_bytes()
    document = json.loads(lock)
    packages = document["packages"]
    for name, package in packages.items():
        if name and (
            not package["resolved"].startswith("https://registry.npmjs.org/")
            or not re.fullmatch(r"sha512-[A-Za-z0-9+/=]+", package["integrity"])
        ):
            raise ValueError("Invalid package origin or integrity in the TypeScript lock")
    folder = checked_directory(user_data_path("setup-system") / "language-tools/typescript-vue")
    folder.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(lock).hexdigest()
    destination = checked_directory(folder / identity)
    if not destination.exists():
        with tempfile.TemporaryDirectory(prefix=".typescript-", dir=folder) as tmp:
            stage = Path(tmp) / "tools"
            stage.mkdir()
            (stage / "package.json").write_text(json.dumps({"private": True, **packages[""]}))
            (stage / "package-lock.json").write_bytes(lock)
            cache = checked_directory(user_cache_path("setup-system") / "npm")
            subprocess.run(
                [
                    "npm",
                    "ci",
                    "--ignore-scripts",
                    "--no-audit",
                    "--no-fund",
                    "--registry=https://registry.npmjs.org",
                    "--userconfig=/dev/null",
                    "--globalconfig=" + str(Path(tmp) / "npm-global.conf"),
                    "--cache",
                    str(cache),
                ],
                cwd=stage,
                check=True,
                timeout=900,
                env={"PATH": os.environ.get("PATH", os.defpath), "HOME": str(Path.home())},
            )
            atomic_write(stage / "modules.sha256", tree_digest(stage / "node_modules").encode())
            os.rename(stage, destination)
    safe_file(destination / "package-lock.json", os.getuid())
    if (destination / "package-lock.json").read_bytes() != lock:
        raise ValueError("Existing TypeScript environment differs from its lock")
    integrity = destination / "modules.sha256"
    safe_file(integrity, os.getuid())
    if not integrity.is_file() or integrity.read_text() != tree_digest(
        destination / "node_modules"
    ):
        raise ValueError("Existing TypeScript files differ from their verified installation")
    commands = {
        name: destination / "node_modules/.bin" / name
        for name in ("typescript-language-server", "vue-language-server", "prettier", "tsc")
    }
    for name, binary in commands.items():
        if not binary.resolve().is_relative_to(destination) or not os.access(binary, os.X_OK):
            raise RuntimeError(f"Missing managed Node executable: {name}")
    plugin = destination / "node_modules/@vue/typescript-plugin"
    if not (plugin / "package.json").is_file():
        raise RuntimeError("Vue TypeScript plugin is unavailable")
    return commands


def _existing_flutter() -> Path | None:
    executable = shutil.which("flutter")
    location = os.environ.get("FLUTTER_ROOT")
    roots = [Path(location)] if location and Path(location).is_absolute() else []
    if executable:
        roots.append(Path(executable).resolve().parent.parent)
    return next(
        (
            path
            for path in roots
            if (
                (path / "packages/flutter/pubspec.yaml").is_file()
                and os.access(path / "bin/flutter", os.X_OK)
                and os.access(path / "bin/cache/dart-sdk/bin/dart", os.X_OK)
            )
        ),
        None,
    )


def language_blocker(root: Path, profile: str) -> str | None:
    """Report the available SDK delivery path before other selected stages can mutate."""
    if profile != "dart_flutter" or _existing_flutter() is not None:
        return None
    from setup_system.capabilities import load_policy

    arch = load_policy(root).normalize_architecture(platform.machine())
    flutter = tomllib.loads((root / "manifests/editor.toml").read_text())["flutter"]
    if f"linux_{arch}_sha256" not in flutter:
        return f"No reviewed Flutter archive for {arch}. Set FLUTTER_ROOT to an existing SDK."
    return None


def _flutter_profile(root: Path, arch: str) -> dict[str, Path]:
    settings = tomllib.loads((root / "manifests/editor.toml").read_text())["flutter"]
    parent = checked_directory(user_data_path("setup-system") / "language-tools/flutter")
    destination = checked_directory(parent / settings["version"])
    selected = _existing_flutter() or destination
    if selected == destination and not destination.exists():
        if f"linux_{arch}_sha256" not in settings:
            raise ValueError(
                f"No reviewed Flutter archive for {arch}. Set FLUTTER_ROOT to an existing SDK."
            )
        parent.mkdir(parents=True, exist_ok=True)
        cache = checked_directory(user_cache_path("setup-system") / "downloads")
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".flutter-", dir=cache) as tmp:
            archive = Path(tmp) / "flutter.tar.xz"
            with archive.open("xb") as stream:
                result = copy_https(
                    settings[f"linux_{arch}_url"],
                    stream,
                    DownloadPolicy(
                        frozenset({"storage.googleapis.com"}), 3 * 1024**3, 60, total_timeout=2400
                    ),
                )
            if result.sha256 != settings[f"linux_{arch}_sha256"]:
                raise RuntimeError("Flutter SDK archive digest mismatch")
            with tempfile.TemporaryDirectory(prefix=".unpack-", dir=parent) as staging:
                with tarfile.open(archive, "r:xz") as bundle:
                    total = 0
                    for member in bundle:
                        parts = Path(member.name).parts
                        total += member.size
                        if (
                            not parts
                            or parts[0] != "flutter"
                            or ".." in parts
                            or total > 8 * 1024**3
                        ):
                            raise ValueError("Invalid Flutter SDK archive layout")
                        bundle.extract(member, staging, filter="data")
                staged = Path(staging) / "flutter"
                atomic_write(
                    staged / "dart-sdk.sha256", tree_digest(staged / "bin/cache/dart-sdk").encode()
                )
                os.rename(staged, destination)
    dart = selected / "bin/cache/dart-sdk/bin/dart"
    flutter = selected / "bin/flutter"
    if not all(os.access(path, os.X_OK) for path in (dart, flutter)):
        raise ValueError("Flutter SDK is missing its executable or bundled Dart SDK")
    if selected == destination:
        integrity = selected / "dart-sdk.sha256"
        safe_file(integrity, os.getuid())
        if not integrity.is_file() or integrity.read_text() != tree_digest(
            selected / "bin/cache/dart-sdk"
        ):
            raise ValueError("Flutter's bundled Dart SDK differs from its verified installation")
        revision = subprocess.run(
            ["git", "-C", str(destination), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        ).stdout.strip()
        if revision != settings["revision"]:
            raise ValueError("Existing Flutter SDK revision differs from the reviewed release")
    return {"dart": dart, "flutter": flutter}


def apply_language_profile(root: Path, profile: str, *, neovim: bool) -> None:
    """Provision just this language. The editor can then check its live LSP handshake."""
    from setup_system.planning import detect_platform

    platform = detect_platform()
    if os.geteuid() == 0 or not platform.implemented:
        raise ValueError("Language profiles require a supported platform and an ordinary user")
    if profile == "python":
        install_python_tools()
        return
    if profile == "typescript_vue":
        commands = _npm_profile()
        for name in ("typescript-language-server", "tsc", "prettier"):
            subprocess.run(
                [commands[name], "--version"], check=True, timeout=20, capture_output=True
            )
    elif profile == "dart_flutter":
        commands = _flutter_profile(root, platform.architecture)
        subprocess.run([commands["dart"], "--version"], capture_output=True, check=True, timeout=30)
        subprocess.run(
            [commands["dart"], "format", "--help"], capture_output=True, check=True, timeout=30
        )
    elif profile == "cpp":
        for name in ("clangd", "clang-format"):
            subprocess.run([name, "--version"], check=True, timeout=15, capture_output=True)
        return
    else:
        raise ValueError("Unknown editor language profile")
    expose_commands(commands)
