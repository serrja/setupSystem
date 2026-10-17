#!/usr/bin/env python3
"""Dependency-free entry point for Debian workstation setup."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

MINIMUM_PYTHON = (3, 10)
UV_VERSION = "0.12.15"
UV_PLATFORMS = {
    ("debian", "x86_64"): "x86_64-unknown-linux-gnu",
    ("debian", "arm64"): "aarch64-unknown-linux-gnu",
}
RELEASE_BASE = f"https://releases.astral.sh/github/uv/releases/download/{UV_VERSION}"
ROOT = Path(__file__).resolve().parent


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

    asset = f"uv-{UV_PLATFORMS[platform_key]}.tar.gz"
    archive = _read_limited(f"{RELEASE_BASE}/{asset}", 80 * 1024 * 1024)
    checksum = _read_limited(f"{RELEASE_BASE}/{asset}.sha256", 4096).decode("ascii").split()[0]
    if not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
        raise RuntimeError("Invalid uv checksum format")
    if hashlib.sha256(archive).hexdigest() != checksum.lower():
        raise RuntimeError("uv archive checksum mismatch")

    target_dir.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or any(
        parent.is_symlink() for parent in target.parents if parent != Path("/")
    ):
        raise RuntimeError("Refusing to install uv through a symlink")
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
            candidate = target_dir / f".uv-{os.getpid()}"
            with candidate.open("wb") as output:
                shutil.copyfileobj(source, output)
            candidate.chmod(0o755)
            os.replace(candidate, target)
    if not matches(str(target)):
        raise RuntimeError("Downloaded uv failed version verification")
    return target


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if sys.version_info < MINIMUM_PYTHON:
        print(
            "Python 3.10+ is required before bootstrap; see README for the OS-specific first step.",
            file=sys.stderr,
        )
        return 2
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / "src"))
    from setup_system.planning import create_plan, format_plan

    if not arguments or arguments[0] in {"-h", "--help"}:
        print(
            "Usage: python3 bootstrap.py {plan|apply} [--profile terminalDev|happ] [--yes]\n"
            "plan is read-only; apply prepares pinned uv and runs the Typer CLI."
        )
        return 0
    profile = "base"
    if "--profile" in arguments:
        position = arguments.index("--profile")
        if position + 1 >= len(arguments) or arguments[position + 1] not in {"terminalDev", "happ"}:
            print("Choose --profile terminalDev or happ.", file=sys.stderr)
            return 2
        profile = arguments[position + 1]
        del arguments[position : position + 2]
    if arguments[0] == "plan" and len(arguments) == 1:
        plan = create_plan(ROOT, profile)
        print(format_plan(plan))
        return 0 if plan.platform.implemented and not plan.blocked else 4
    if arguments[0] != "apply" or any(arg != "--yes" for arg in arguments[1:]):
        print("Unknown bootstrap arguments. Use --help.", file=sys.stderr)
        return 2
    plan = create_plan(ROOT, profile)
    if not plan.platform.implemented or plan.blocked:
        print(format_plan(plan), file=sys.stderr)
        return 4
    if "--yes" not in arguments:
        if not sys.stdin.isatty():
            print("apply needs --yes in a non-interactive session.", file=sys.stderr)
            return 2
        print(format_plan(plan))
        if input("Apply user configuration? [y/N] ").strip().lower() not in {"y", "yes"}:
            return 2
    try:
        uv = _managed_uv((plan.platform.distribution, plan.platform.architecture))
        subprocess.run([str(uv), "python", "install", "3.14"], cwd=ROOT, timeout=600, check=True)
        with tempfile.TemporaryDirectory(prefix="setup-system-wheel-") as temporary:
            stage = Path(temporary)
            subprocess.run(
                [str(uv), "build", "--wheel", "--out-dir", str(stage)],
                cwd=ROOT,
                timeout=300,
                check=True,
            )
            wheels = list(stage.glob("setup_system-*.whl"))
            if len(wheels) != 1:
                raise RuntimeError("Expected one setup-system wheel")
            requirements = stage / "runtime.txt"
            subprocess.run(
                [
                    str(uv),
                    "export",
                    "--locked",
                    "--no-dev",
                    "--no-emit-project",
                    "--format",
                    "requirements.txt",
                    "--output-file",
                    str(requirements),
                    "--quiet",
                ],
                cwd=ROOT,
                timeout=120,
                check=True,
            )
            subprocess.run(
                [
                    str(uv),
                    "tool",
                    "install",
                    "--python",
                    "3.14",
                    "--reinstall-package",
                    "setup-system",
                    "--with-requirements",
                    str(requirements),
                    str(wheels[0]),
                ],
                cwd=ROOT,
                timeout=300,
                check=True,
            )
        subprocess.run(
            [
                str(uv),
                "run",
                "--locked",
                "--managed-python",
                "--python",
                "3.14",
                "setup-system",
                "apply",
                "--profile",
                profile,
                "--yes",
            ],
            cwd=ROOT,
            timeout=900,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"Bootstrap delegated apply returned {exc.returncode}.", file=sys.stderr)
        return exc.returncode
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"Bootstrap failed: {exc}", file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
