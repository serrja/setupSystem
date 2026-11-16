#!/usr/bin/env python3
"""Dependency-free entry point for Debian workstation setup."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

MINIMUM_PYTHON = (3, 11)
RUNTIME_PYTHON = "3.14.7"
UV_VERSION = "0.12.15"
UV_PLATFORMS = {
    ("debian", "x86_64"): "x86_64-unknown-linux-gnu",
    ("debian", "arm64"): "aarch64-unknown-linux-gnu",
}
RELEASE_BASE = f"https://releases.astral.sh/github/uv/releases/download/{UV_VERSION}"
ROOT = Path(__file__).resolve().parent
UV_SHA256 = {
    "x86_64-unknown-linux-gnu": "f97935763c04be3e692460a7aaeaaab8fc3b78fcf8b389da820b38ae7423a638",
    "aarch64-unknown-linux-gnu": "0e9a3499b0587d449c9ff684c0160da607826e4af1cee220bc87f378702d3e08",
}


def _read_limited(url: str, limit: int) -> bytes:
    with urlopen(url, timeout=30) as response:
        final = urlsplit(response.geturl())
        if final.scheme != "https" or final.hostname not in {
            "releases.astral.sh",
            "github.com",
            "objects.githubusercontent.com",
            "release-assets.githubusercontent.com",
        }:
            raise RuntimeError("Unexpected uv release redirect")
        data = bytes(response.read(limit + 1))
    if len(data) > limit:
        raise RuntimeError("uv release asset exceeds its size limit")
    return data


def _managed_uv(platform_key: tuple[str, str]) -> Path:
    if os.geteuid() == 0:
        raise RuntimeError("User runtime must never be prepared as root")

    def matches(path: str) -> bool:
        result = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=10, check=False
        )
        parts = result.stdout.strip().split()
        return result.returncode == 0 and len(parts) >= 2 and parts[:2] == ["uv", UV_VERSION]

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

    asset = f"uv-{UV_PLATFORMS[platform_key]}.tar.gz"
    archive = _read_limited(f"{RELEASE_BASE}/{asset}", 80 * 1024 * 1024)
    checksum = UV_SHA256[UV_PLATFORMS[platform_key]]
    if not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
        raise RuntimeError("Invalid uv checksum format")
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install the managed Hyprland profile on clean Debian."
    )
    parser.add_argument("command", nargs="?", choices=("plan", "apply"), default="apply")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Explicit unattended apply consent")
    parser.add_argument("--user", help="Target account; required for a direct root invocation")
    for key in ("config", "data", "state", "cache"):
        parser.add_argument(f"--xdg-{key}-home", type=Path)
    return parser


def _user_runtime() -> int:
    """Prepare the locked environment and apply files only after losing root privileges."""
    from setup_system.installation import resolve_target, run_command, validate_user_path
    from setup_system.planning import detect_platform

    target = resolve_target(None)
    if os.geteuid() == 0:
        raise RuntimeError("Refusing to build or configure a desktop as root")
    platform = detect_platform()
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
    from setup_system.storage import atomic_write

    receipt = validate_user_path(data / "setup-system/runtime.json", target)
    expected_receipt = b'{"owner":"setupSystem","runtimePython":"3.14.7"}\n'
    if receipt.exists() and receipt.read_bytes() != expected_receipt:
        raise ValueError("Existing runtime ownership receipt differs; review it before retrying")
    if runtime.exists() and (
        not runtime.is_dir() or (any(runtime.iterdir()) and not receipt.is_file())
    ):
        raise ValueError("Existing runtime directory is unmanaged; review it before retrying")
    atomic_write(receipt, expected_receipt)
    os.environ.update({key: str(path) for key, path in destinations.items()})
    # The source bundle and lock are inputs. No root-owned checkout venv is created.
    uv = _managed_uv((platform.distribution, platform.architecture))
    run_command([str(uv), "python", "install", RUNTIME_PYTHON], cwd=ROOT, timeout=600, check=True)
    run_command(
        [
            str(uv),
            "sync",
            "--locked",
            "--no-dev",
            "--no-editable",
            "--managed-python",
            "--python",
            RUNTIME_PYTHON,
            "--project",
            str(ROOT),
        ],
        cwd=ROOT,
        timeout=900,
        check=True,
    )
    result = run_command(
        [
            str(runtime / "bin/python"),
            "-I",
            "-B",
            str(ROOT / "src/setup_system/user_install.py"),
            str(ROOT),
        ],
        cwd=target.home,
        timeout=600,
        check=False,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    internal = arguments[:1] == ["_user-stage"]
    if internal:
        if os.geteuid() == 0:
            print("Refusing root user-stage.", file=sys.stderr)
            return 2
        arguments[0] = "apply"
    options = _parser().parse_args(arguments)
    if options.dry_run:
        options.command = "plan"
    if options.command == "plan" and options.yes:
        print("--yes belongs to apply.", file=sys.stderr)
        return 2
    if sys.version_info < MINIMUM_PYTHON:
        print(
            "System Python 3.11+ is required; use sudo bash ./setup.sh to prepare it.",
            file=sys.stderr,
        )
        return 2
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / "src"))
    from setup_system.installation import (
        SystemPaths,
        current_packages,
        install_system,
        mark_user_stage,
        resolve_target,
        run_command,
        user_environment,
        validate_installation_paths,
    )

    try:
        packages = current_packages(ROOT)
    except (OSError, ValueError) as exc:
        print(f"UNSUPPORTED: {exc}. No changes made.", file=sys.stderr)
        return 4
    try:
        target = resolve_target(options.user)
        overrides = {
            key: value
            for key in ("config", "data", "state", "cache")
            if (value := getattr(options, f"xdg_{key}_home")) is not None
        }
        environment = user_environment(target, overrides)
        validate_installation_paths(ROOT, target, environment)
        for path in (
            "uv.lock",
            "pyproject.toml",
            "manifests/platforms.toml",
            "assets/hyprland/profile.lua",
            "src/setup_system/user_install.py",
        ):
            if not (ROOT / path).is_file():
                raise ValueError("Incomplete local source bundle")
    except (OSError, ValueError) as exc:
        print(f"Preflight failed: {exc}", file=sys.stderr)
        return 2
    if internal:
        # Parent supplies a closed environment; the current passwd UID is checked again.
        os.environ.update(environment)
        try:
            return _user_runtime()
        except (
            OSError,
            ValueError,
            RuntimeError,
            subprocess.SubprocessError,
            KeyboardInterrupt,
        ) as exc:
            print(
                f"FAILED user runtime/configuration: {type(exc).__name__}: {exc}. "
                "Review the reported prerequisite/conflict and re-run the same command.",
                file=sys.stderr,
            )
            return 5
    print(
        f"Hyprland plan for {target.name} (uid={target.uid}, gid={target.gid}), HOME={target.home}"
    )
    print(
        "System: isolated signed Debian refresh; exact backports pins; "
        "no removals or service restarts."
    )
    print("Packages: python3 ca-certificates " + " ".join(packages))
    print(
        "User: pinned uv "
        + UV_VERSION
        + "/Python "
        + RUNTIME_PYTHON
        + ", locked runtime, managed files with backups."
    )
    print("User configuration: " + environment["XDG_CONFIG_HOME"])
    print("Preserve network, locale, timezone, login shell and session. No automatic login/reboot.")
    print(
        "Availability/signatures are checked after authorized refresh during apply; "
        "this plan has no writes."
    )
    if options.command == "plan":
        return 0
    if os.geteuid() != 0:
        print("Use sudo bash ./setup.sh for installation.", file=sys.stderr)
        return 2
    if not options.yes:
        if not sys.stdin.isatty():
            print("No TTY: apply requires explicit --yes consent.", file=sys.stderr)
            return 2
        if input("Apply the complete plan? [y/N] ").strip().lower() not in {"y", "yes"}:
            return 2
    paths = SystemPaths()
    user_started = False
    try:
        install_system(ROOT, paths=paths)
        print(
            f"Stage: user runtime/configuration (uid={target.uid}; no root build hooks)", flush=True
        )
        user_started = True
        forwarded = ["--user", target.name, "--yes"]
        for key, value in overrides.items():
            forwarded += [f"--xdg-{key}-home", str(value)]
        result = run_command(
            [sys.executable, "-I", "-B", str(ROOT / "bootstrap.py"), "_user-stage", *forwarded],
            cwd=target.home,
            env=environment,
            user=target.uid,
            group=target.gid,
            extra_groups=os.getgrouplist(target.name, target.gid),
            umask=0o077,
            timeout=2400,
            termination_grace=15,
            check=False,
        )
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        mark_user_stage(paths, "complete" if result.returncode == 0 else "failed")
        if result.returncode:
            print(
                "FAILED user stage. System packages remain installed; "
                "fix the reported error and re-run the same command.",
                file=sys.stderr,
            )
            return result.returncode if result.returncode > 0 else 5
    except (
        OSError,
        ValueError,
        RuntimeError,
        subprocess.SubprocessError,
        KeyboardInterrupt,
    ) as exc:
        if user_started and paths.journal.exists():
            mark_user_stage(paths, "failed")
        print(
            f"FAILED installation: {type(exc).__name__}. See {paths.journal}; "
            "no APT rollback is promised.",
            file=sys.stderr,
        )
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(str(exc.stderr)[-2000:], file=sys.stderr)
        return 5
    launcher = Path(environment["XDG_DATA_HOME"]) / "setup-system/bin/start-hyprland"
    recovery = launcher.parent.parent / "runtime/bin/setup-system"
    print("Installation completed; REAL LOGIN/GUI must be checked on the target machine.")
    print(
        "Next: log in locally on a TTY as "
        + target.name
        + " and run: "
        + shlex.quote(str(launcher))
    )
    print("Return to TTY: Super+F9 then Logout; switch consoles with Ctrl+Alt+F3.")
    print("Recovery preview: " + shlex.quote(str(recovery)) + " workstation restore BACKUP_ID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
