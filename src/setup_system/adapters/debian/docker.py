"""Narrow Docker Engine installation from Docker's signed Debian repository."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from setup_system.planning import detectPlatform

KEY_URL = "https://download.docker.com/linux/debian/gpg"
KEY_FINGERPRINT = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
KEY_PATH = Path("/etc/apt/keyrings/docker.asc")
SOURCE_PATH = Path("/etc/apt/sources.list.d/docker.sources")
PACKAGES = (
    "docker-ce",
    "docker-ce-cli",
    "containerd.io",
    "docker-buildx-plugin",
    "docker-compose-plugin",
)
CONFLICTS = (
    "docker.io",
    "docker-compose",
    "docker-doc",
    "docker-buildx",
    "podman-docker",
    "containerd",
    "runc",
)


def _runCommand(argv: list[str], *, timeout: int = 120, env: dict[str, str] | None = None) -> str:
    safeEnv = {
        key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "LANG", "LC_ALL"}
    }
    if env:
        safeEnv.update(env)
    result = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=False, env=safeEnv
    )
    if result.returncode:
        raise RuntimeError(f"{Path(argv[0]).name} failed with exit code {result.returncode}")
    return result.stdout


def _installedPackage(package: str) -> bool:
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${Status}", package],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return result.returncode == 0 and result.stdout == "install ok installed"


def _verifyKey(path: Path, temporaryHome: Path) -> None:
    env = {"GNUPGHOME": str(temporaryHome), "LC_ALL": "C"}
    output = _runCommand(["gpg", "--show-keys", "--with-colons", str(path)], timeout=15, env=env)
    fingerprints = [line.split(":")[9] for line in output.splitlines() if line.startswith("fpr:")]
    if not fingerprints or fingerprints[0] != KEY_FINGERPRINT:
        raise RuntimeError("Docker signing key fingerprint mismatch")


def _candidateVersion(package: str) -> str:
    output = _runCommand(["apt-cache", "policy", package], timeout=15, env={"LC_ALL": "C"})
    match = re.search(r"^\s*Candidate:\s*(\S+)", output, re.MULTILINE)
    if match is None or match.group(1) == "(none)":
        raise RuntimeError(f"No Debian repository candidate for {package}")
    return match.group(1)


def installDocker() -> str:
    """Install only on exact Debian, without replacing existing Docker data or packages."""
    platform = detectPlatform()
    if not platform.implemented or platform.distribution != "debian":
        raise ValueError(platform.reason)
    codename = {"12": "bookworm", "13": "trixie"}[platform.version]
    fields = dict(
        line.split("=", 1)
        for line in Path("/etc/os-release").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    if fields.get("VERSION_CODENAME", "").strip('"') != codename:
        raise RuntimeError("Debian version and codename disagree")
    architecture = _runCommand(["dpkg", "--print-architecture"], timeout=10).strip()
    if architecture != {"x86_64": "amd64", "arm64": "arm64"}[platform.architecture]:
        raise RuntimeError("Debian architecture and dpkg architecture disagree")
    if _installedPackage("docker-ce"):
        if any(_installedPackage(package) for package in CONFLICTS):
            raise RuntimeError("Existing Docker installation has conflicting packages")
        if not all(_installedPackage(package) for package in PACKAGES):
            raise RuntimeError("Existing Docker installation is incomplete; review before updating")
        return "unchanged: Docker Engine already installed; no update performed"
    if any(_installedPackage(package) for package in CONFLICTS + PACKAGES[1:]):
        raise RuntimeError("Existing Docker/container packages need a separate migration review")
    if shutil.which("docker") or shutil.which("containerd"):
        raise RuntimeError("Existing Docker/container binaries need a separate source review")
    if shutil.which("sudo") is None or shutil.which("gpg") is None:
        raise RuntimeError("Targeted sudo and gpg are required for Docker repository verification")
    _runCommand(["sudo", "-n", "true"], timeout=10)
    source = (
        "Types: deb\n"
        "URIs: https://download.docker.com/linux/debian\n"
        f"Suites: {codename}\n"
        "Components: stable\n"
        f"Architectures: {architecture}\n"
        f"Signed-By: {KEY_PATH}\n"
    )
    if SOURCE_PATH.is_symlink() or KEY_PATH.is_symlink():
        raise RuntimeError("Docker APT source or key path is a symlink")
    if SOURCE_PATH.exists() and SOURCE_PATH.read_text() != source:
        raise RuntimeError("Existing Docker APT source differs; refusing to overwrite it")
    with tempfile.TemporaryDirectory(prefix="setup-system-docker-") as directory:
        stage = Path(directory)
        stage.chmod(0o700)
        key = stage / "docker.asc"
        if KEY_PATH.exists():
            _verifyKey(KEY_PATH, stage)
        else:
            with urlopen(KEY_URL, timeout=30) as response:
                target = urlsplit(response.geturl())
                if target.scheme != "https" or target.hostname != "download.docker.com":
                    raise RuntimeError("Unexpected Docker key redirect")
                content = response.read(64 * 1024 + 1)
            if len(content) > 64 * 1024:
                raise RuntimeError("Docker signing key exceeds size limit")
            key.write_bytes(content)
            _verifyKey(key, stage)
            _runCommand(["sudo", "-n", "install", "-m", "0755", "-d", str(KEY_PATH.parent)])
            _runCommand(["sudo", "-n", "install", "-m", "0644", str(key), str(KEY_PATH)])
        if not SOURCE_PATH.exists():
            sourceFile = stage / "docker.sources"
            sourceFile.write_text(source)
            _runCommand(["sudo", "-n", "install", "-m", "0755", "-d", str(SOURCE_PATH.parent)])
            _runCommand(["sudo", "-n", "install", "-m", "0644", str(sourceFile), str(SOURCE_PATH)])
    aptEnv = {"LC_ALL": "C", "DEBIAN_FRONTEND": "noninteractive"}
    _runCommand(["sudo", "-n", "apt-get", "update"], timeout=600, env=aptEnv)
    versions = {package: _candidateVersion(package) for package in PACKAGES}
    _runCommand(
        [
            "sudo",
            "-n",
            "apt-get",
            "install",
            "-y",
            *(f"{name}={version}" for name, version in versions.items()),
        ],
        timeout=1200,
        env=aptEnv,
    )
    if not all(_installedPackage(package) for package in PACKAGES):
        raise RuntimeError("Docker packages were not all detected after installation")
    return (
        "changed: Docker Engine, CLI, containerd, Buildx and Compose installed; "
        "daemon and group not verified"
    )
