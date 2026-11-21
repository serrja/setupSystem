"""Pinned user runtime preparation; no privileged builds or installation CLI."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from setup_system.bootstrap.user_policy import bootstrap_architecture, runtime_pins
from setup_system.infrastructure.downloads import (
    UV_RELEASE_HOSTS,
    DownloadError,
    DownloadFailure,
    DownloadPolicy,
    ProxyPolicy,
    read_https,
)


def _runtime_receipt(python: str) -> bytes:
    return (
        json.dumps({"owner": "setupSystem", "runtimePython": python}, separators=(",", ":")) + "\n"
    ).encode()


def _read_limited(url: str, limit: int) -> bytes:
    policy = DownloadPolicy(
        UV_RELEASE_HOSTS, limit, 30, ProxyPolicy.from_loopback_environment(os.environ)
    )
    try:
        return read_https(url, policy)
    except DownloadError as exc:
        if exc.kind == DownloadFailure.SIZE:
            raise RuntimeError("uv release asset exceeds its size limit") from None
        raise RuntimeError(f"Pinned uv download: {exc}") from None


def managed_uv(platform_key: tuple[str, str], *, root: Path | None = None) -> Path:
    if os.geteuid() == 0:
        raise RuntimeError("User runtime must never be prepared as root")

    if platform_key[0] != "debian":
        raise ValueError("No reviewed runtime for this distribution")
    pins = runtime_pins(root)
    arch = bootstrap_architecture(root, platform_key[1])

    def matches(path: str) -> bool:
        result = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=10, check=False
        )
        parts = result.stdout.strip().split()
        return result.returncode == 0 and len(parts) >= 2 and parts[:2] == ["uv", pins.uv]

    executable = shutil.which("uv")
    if executable and matches(executable):
        return Path(executable)

    target_dir = (
        Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "setup-system/bin"
    )
    target = target_dir / "uv"
    if target.is_file() and not target.is_symlink() and matches(str(target)):
        return target

    if target.exists():
        raise RuntimeError(
            "Existing uv is unmanaged or has a different version; review it before retrying"
        )

    triple, checksum = pins.artifact(arch)
    asset = f"uv-{triple}.tar.gz"
    archive = _read_limited(f"{pins.uv_base}/{pins.uv}/{asset}", 80 * 1024 * 1024)
    if hashlib.sha256(archive).hexdigest() != checksum.lower():
        raise RuntimeError("uv archive checksum mismatch")

    if target.is_symlink() or any(
        parent.is_symlink() for parent in target.parents if parent != Path("/")
    ):
        raise RuntimeError("Refusing to install uv through a symlink")
    target_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="setup-system-uv-") as staging:
        archive_path = Path(staging) / asset
        archive_path.write_bytes(archive)
        with tarfile.open(archive_path, "r:gz") as bundle:
            members = [
                member
                for member in bundle.getmembers()
                if member.name.endswith("/uv") and member.isfile()
            ]
            if len(members) != 1:
                raise RuntimeError("uv archive does not contain one regular uv binary")
            source = bundle.extractfile(members[0])
            if source is None:
                raise RuntimeError("Could not read uv binary")
            descriptor, name = tempfile.mkstemp(prefix=".uv-", dir=target_dir)
            candidate = Path(name)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    shutil.copyfileobj(source, output)
                candidate.chmod(0o755)
                os.replace(candidate, target)
            finally:
                candidate.unlink(missing_ok=True)
    if not matches(str(target)):
        raise RuntimeError("Downloaded uv failed version verification")
    return target


class UserRuntime:
    """Adapt a verified source bundle to one owned, locked user environment."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def lock(self) -> str | None:
        """Reconcile only an allocated root version; uv still checks all lock metadata."""
        for name in ("pyproject.toml", "uv.lock"):
            path = self.root / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("Runtime metadata must be bounded regular local files")
        project = tomllib.loads((self.root / "pyproject.toml").read_text())
        text = (self.root / "uv.lock").read_text()
        lock = tomllib.loads(text)
        metadata = project.get("project")
        if not isinstance(metadata, dict):
            raise ValueError("Missing runtime project metadata")
        version = metadata.get("version")
        if metadata.get("name") != "setup-system" or not isinstance(version, str):
            raise ValueError("Unexpected runtime project identity")
        if not re.fullmatch(
            r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:\.dev(?:0|[1-9][0-9]*))?",
            version,
        ):
            raise ValueError("Invalid native runtime version")
        packages = lock.get("package")
        if not isinstance(packages, list) or any(not isinstance(item, dict) for item in packages):
            raise ValueError("Missing runtime lock packages")
        roots = [item for item in packages if item.get("name") == "setup-system"]
        if len(roots) != 1 or roots[0].get("source") != {"editable": "."}:
            raise ValueError("Lock must contain one local setup-system root")
        if roots[0].get("version") == version:
            return None
        normalized, count = re.subn(
            r'(?m)(^\[\[package\]\]\r?\nname = "setup-system"\r?\nversion = )"[^"\r\n]+"',
            lambda match: match[1] + '"' + version + '"',
            text,
        )
        roots[0]["version"] = version
        if count != 1 or tomllib.loads(normalized) != lock:
            raise ValueError(
                "Cannot reconcile the local root version without changing dependencies"
            )
        return normalized

    def inputs(self) -> list[tuple[Path, bool]]:
        """Validate the finite build-input tree without creating files or running hooks."""
        entries: list[tuple[Path, bool]] = []
        size = 0

        def collect(relative: Path) -> None:
            nonlocal size
            source = self.root / relative
            info = source.lstat()
            directory = stat.S_ISDIR(info.st_mode)
            if not directory and not stat.S_ISREG(info.st_mode):
                raise ValueError("Runtime build inputs cannot contain symlinks or special files")
            if len(relative.parts) == 1 and directory != (
                relative.name in {"src", "assets", "manifests"}
            ):
                raise ValueError("Runtime build input has an unexpected file/directory type")
            entries.append((relative, directory))
            if not directory:
                size += info.st_size
            if len(entries) > 4096 or size > 32 * 1024 * 1024:
                raise ValueError("Runtime build input exceeds its size or entry limit")
            if directory:
                for child in sorted(source.iterdir()):
                    collect(relative / child.name)

        for name in (
            "pyproject.toml",
            "uv.lock",
            "README.md",
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
            "src",
            "assets",
            "manifests",
        ):
            collect(Path(name))
        return entries

    @contextmanager
    def project(self, cache: Path) -> Iterator[Path]:
        """Build allocated source bundles privately without changing source or dependency pins."""
        if os.geteuid() == 0:
            raise RuntimeError("Runtime build staging must never run as root")
        normalized = self.lock()
        if normalized is None:
            yield self.root
            return
        entries = self.inputs()
        if (
            not cache.is_absolute()
            or ".." in cache.parts
            or any(path.is_symlink() for path in (cache, *cache.parents))
        ):
            raise ValueError("Unsafe runtime build cache")
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="build-source-", dir=cache) as temporary:
            stage = Path(temporary)
            for relative, directory in entries:
                if directory:
                    (stage / relative).mkdir()
                elif relative == Path("uv.lock"):
                    (stage / relative).write_text(normalized)
                else:
                    shutil.copy2(self.root / relative, stage / relative)
            yield stage

    def verify_prepared(self) -> Path:
        """Check the installed command and the ownership receipt after uv completes."""
        from setup_system.infrastructure.identity import resolve_target, validate_user_path

        if os.geteuid() == 0:
            raise RuntimeError("User runtime cannot be used as root")
        target = resolve_target(None)
        data = validate_user_path(Path(os.environ["XDG_DATA_HOME"]), target)
        runtime = validate_user_path(data / "setup-system/runtime", target)
        receipt = validate_user_path(data / "setup-system/runtime.json", target)
        expected = _runtime_receipt(runtime_pins(self.root).python)
        if not runtime.is_dir() or not receipt.is_file() or receipt.read_bytes() != expected:
            raise ValueError("User runtime is not prepared; re-run the same installation command")
        command = validate_user_path(runtime / "bin/setup-system", target)
        if not command.is_file() or not os.access(command, os.X_OK):
            raise ValueError("Prepared setup-system CLI is not executable")
        return runtime

    def prepare(self) -> Path:
        """Prepare the locked environment only after losing root privileges."""
        from setup_system.infrastructure.identity import resolve_target, validate_user_path
        from setup_system.infrastructure.processes import run_command

        target = resolve_target(None)
        if os.geteuid() == 0:
            raise RuntimeError("Refusing to build or configure a desktop as root")
        machine = platform.machine()
        bootstrap_architecture(self.root, machine, check_host=True)
        pins = runtime_pins(self.root)
        data = validate_user_path(Path(os.environ["XDG_DATA_HOME"]), target)
        cache = validate_user_path(Path(os.environ["XDG_CACHE_HOME"]), target)
        runtime = validate_user_path(data / "setup-system/runtime", target)
        destinations = {
            "UV_PROJECT_ENVIRONMENT": runtime,
            "UV_CACHE_DIR": cache / "setup-system/uv",
            "UV_PYTHON_INSTALL_DIR": data / "setup-system/python",
            "UV_PYTHON_BIN_DIR": data / "setup-system/bin",
        }
        for path in destinations.values():
            validate_user_path(path, target)
            if path.exists() and not path.is_dir():
                raise ValueError("Runtime destinations must be directories")
        for path in (data / "setup-system", cache / "setup-system"):
            validate_user_path(path, target).mkdir(parents=True, exist_ok=True)
        from setup_system.infrastructure.user_storage import atomic_write

        receipt = validate_user_path(data / "setup-system/runtime.json", target)
        expected_receipt = _runtime_receipt(pins.python)
        if receipt.exists() and receipt.read_bytes() != expected_receipt:
            raise ValueError(
                "Existing runtime ownership receipt differs; review it before retrying"
            )
        if runtime.exists() and (
            not runtime.is_dir() or (any(runtime.iterdir()) and not receipt.is_file())
        ):
            raise ValueError("Existing runtime directory is unmanaged; review it before retrying")
        atomic_write(receipt, expected_receipt)
        os.environ.update({key: str(path) for key, path in destinations.items()})
        # The source bundle and lock are inputs. No root-owned checkout venv is created.
        uv = managed_uv(("debian", machine), root=self.root)
        run_command(
            [str(uv), "python", "install", pins.python], cwd=self.root, timeout=600, check=True
        )
        with self.project(cache / "setup-system") as project:
            run_command(
                [
                    str(uv),
                    "sync",
                    "--locked",
                    "--no-dev",
                    "--no-editable",
                    "--reinstall-package",
                    "setup-system",
                    "--managed-python",
                    "--python",
                    pins.python,
                    "--project",
                    str(project),
                ],
                cwd=project,
                timeout=900,
                check=True,
            )
        return self.verify_prepared()
