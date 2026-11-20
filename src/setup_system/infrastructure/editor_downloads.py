"""Install pinned terminal tools without executing installers at shell or editor startup."""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import stat
import subprocess
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path

from platformdirs import user_cache_path

from setup_system.downloads import (
    GITHUB_RELEASE_HOSTS,
    DownloadError,
    DownloadFailure,
    DownloadPolicy,
    ProxyPolicy,
    copy_https,
)
from setup_system.infrastructure.config_store import safe_file
from setup_system.infrastructure.file_receipts import checked_directory
from setup_system.infrastructure.user_files import apply_files
from setup_system.planning import Platform
from setup_system.storage import atomic_write

MAX_NEOVIM_ARCHIVE_BYTES = 120 * 1024 * 1024
MAX_TOOL_ARCHIVE_BYTES = 100 * 1024 * 1024


def _manifest(project_root: Path) -> dict[str, object]:
    return tomllib.loads((project_root / "manifests/editor.toml").read_text())


def _data_home() -> Path:
    home = Path.home().resolve()
    target = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")).expanduser().absolute()
    if not target.is_relative_to(home) or any(
        parent.is_symlink() for parent in target.parents if parent != Path("/")
    ):
        raise ValueError("XDG_DATA_HOME must be inside the current home without symlink parents")
    return checked_directory(target)


def _run(argv: list[str], *, cwd: Path | None = None, timeout: int = 180) -> str:
    safe_env = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "HOME",
            "PATH",
            "LANG",
            "LC_ALL",
            "TMPDIR",
            "SSL_CERT_FILE",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_STATE_HOME",
            "XDG_CACHE_HOME",
            "XDG_RUNTIME_DIR",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "NO_PROXY",
            "https_proxy",
            "http_proxy",
            "no_proxy",
        }
    }
    safe_env.update(
        GIT_TERMINAL_PROMPT="0",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
    )
    if argv[0] == "git":
        argv = [
            "git",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "core.fsmonitor=false",
            *argv[1:],
        ]
    result = subprocess.run(
        argv, cwd=cwd, env=safe_env, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {argv[0]} {argv[1] if len(argv) > 1 else ''}"
        )
    return result.stdout.strip()


def _download_archive(
    url: str,
    destination: Path,
    limit: int,
    *,
    redirect_error: str,
    size_error: str,
) -> str:
    """Share bounded release transport while retaining each provider's diagnostics."""
    policy = DownloadPolicy(
        GITHUB_RELEASE_HOSTS,
        limit,
        30,
        proxy=ProxyPolicy.from_loopback_environment(os.environ),
    )
    try:
        with destination.open("wb") as output:
            return copy_https(url, output, policy).sha256
    except DownloadError as exc:
        if exc.kind == DownloadFailure.URL:
            raise RuntimeError(redirect_error) from None
        if exc.kind == DownloadFailure.SIZE:
            raise RuntimeError(size_error) from None
        raise RuntimeError("Pinned terminal tool download failed: " + str(exc)) from None


def _digest_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _archive(entry: dict[str, object], platform: Platform, limit: int) -> Path:
    if not platform.implemented or platform.distribution != "debian":
        raise ValueError(platform.reason)
    key = "linux_" + platform.architecture
    value = entry.get(key + "_sha256")
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("No reviewed archive for this platform")
    path = checked_directory(user_cache_path("setup-system") / "downloads") / value
    safe_file(path, os.getuid())
    if path.exists():
        if path.stat().st_size > limit or _digest_file(path) != value:
            raise ValueError("Cached editor archive digest mismatch")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".download-", dir=path.parent) as tmp:
        stage = Path(tmp) / "archive"
        found = _download_archive(
            str(entry[key + "_url"]),
            stage,
            limit,
            redirect_error="Unexpected editor archive redirect",
            size_error="Editor archive exceeds size limit",
        )
        if found != value:
            raise RuntimeError("Editor archive digest mismatch")
        os.replace(stage, path)
    return path


def _tree(root: Path) -> dict[Path, tuple[int, str]]:
    result = {}
    for path in root.rglob("*"):
        mode = path.lstat().st_mode
        if stat.S_ISREG(mode):
            value = _digest_file(path)
        elif stat.S_ISLNK(mode):
            value = os.readlink(path)
        elif stat.S_ISDIR(mode):
            value = ""
        else:
            raise ValueError("Unexpected entry in the editor runtime")
        result[path.relative_to(root)] = (stat.S_IFMT(mode) | (mode & 0o111), value)
    return result


def tree_digest(root: Path) -> str:
    """Record executable modes, link targets and file contents for a private installed tool."""
    digest = hashlib.sha256()
    for path, (mode, value) in sorted(_tree(root).items()):
        for part in (str(path), str(mode), value):
            digest.update(os.fsencode(part) + b"\0")
    return digest.hexdigest()


def expose_commands(commands: dict[str, Path]) -> None:
    """Keep verified legacy links and use recoverable script wrappers for new commands."""
    bindir = checked_directory(Path.home() / ".local/bin")
    targets = {}
    for name, binary in commands.items():
        link = bindir / name
        if link.is_symlink() and link.resolve() == binary.resolve():
            continue
        if link == binary:
            continue
        targets[link] = ("#!/bin/sh\nexec " + shlex.quote(str(binary)) + ' "$@"\n').encode()
    if targets:
        apply_files(targets, modes=dict.fromkeys(targets, 0o755))


def install_neovim(project_root: Path, platform: Platform) -> str:
    """Compare an existing runtime with the verified upstream archive before execution."""
    entry = _manifest(project_root)["neovim"]
    if not isinstance(entry, dict):
        raise ValueError("Invalid Neovim release")
    version = str(entry["version"])
    if not re.fullmatch(r"[0-9]+[.][0-9]+[.][0-9]+", version):
        raise ValueError("Invalid Neovim version")
    archive = _archive(entry, platform, MAX_NEOVIM_ARCHIVE_BYTES)
    target = checked_directory(_data_home() / "setup-system/neovim" / version)
    top = "nvim-linux-" + platform.architecture
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".neovim-", dir=target.parent) as tmp:
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            if sum(member.size for member in members) > 512 * 1024**2:
                raise ValueError("Editor runtime exceeds extraction limit")
            for member in members:
                parts = Path(member.name).parts
                if not parts or parts[0] != top or ".." in parts:
                    raise ValueError("Unsafe Neovim archive path")
            bundle.extractall(tmp, members=members, filter="data")
        reference = Path(tmp) / top
        if not (reference / "bin/nvim").is_file():
            raise ValueError("Editor archive is missing Neovim")
        if target.exists():
            if _tree(reference) != _tree(target):
                raise ValueError("Existing editor runtime differs from the verified release")
            status = "unchanged"
        else:
            os.rename(reference, target)
            status = "changed"
    binary = target / "bin/nvim"
    if _run([str(binary), "--version"], timeout=15).splitlines()[:1] != [f"NVIM v{version}"]:
        raise RuntimeError("Neovim version verification failed")
    expose_commands({"nvim": binary})
    return f"{status}: Neovim {version}"


def install_plugins(project_root: Path) -> list[str]:
    """Check out exact public revisions into Neovim's standard user data path."""
    plugins = _manifest(project_root)["plugins"]
    if not isinstance(plugins, dict):
        raise ValueError("Invalid plugin manifest")
    destination = checked_directory(_data_home() / "nvim/lazy")
    destination.mkdir(parents=True, exist_ok=True)
    results: list[str] = []
    for name, entry in plugins.items():
        if not isinstance(entry, dict):
            raise ValueError("Invalid plugin entry")
        repo = str(entry["repo"])
        commit = str(entry["commit"])
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not re.fullmatch(
            r"[0-9a-f]{40}", commit
        ):
            raise ValueError("Invalid plugin source or commit")
        folder_name = "lazy.nvim" if name == "lazy" else name
        folder = destination / folder_name
        if folder.is_symlink():
            raise ValueError(f"Plugin path is a symlink: {folder}")
        if folder.exists():
            actual = _run(["git", "-C", str(folder), "rev-parse", "HEAD"], timeout=15)
            dirty = _run(["git", "-C", str(folder), "status", "--porcelain"], timeout=15)
            if actual != commit or dirty:
                raise ValueError(f"Existing plugin differs from pinned revision: {name}")
            results.append(f"unchanged: {name} {commit[:12]}")
            continue
        with tempfile.TemporaryDirectory(prefix=f".{folder_name}-", dir=destination) as temporary:
            stage = Path(temporary) / "checkout"
            stage.mkdir()
            _run(["git", "init", "-q", str(stage)], timeout=15)
            _run(
                [
                    "git",
                    "-C",
                    str(stage),
                    "remote",
                    "add",
                    "origin",
                    f"https://github.com/{repo}.git",
                ],
                timeout=15,
            )
            _run(["git", "-C", str(stage), "fetch", "--depth", "1", "origin", commit], timeout=180)
            _run(["git", "-C", str(stage), "checkout", "-q", "--detach", "FETCH_HEAD"], timeout=30)
            actual = _run(["git", "-C", str(stage), "rev-parse", "HEAD"], timeout=15)
            if actual != commit:
                raise RuntimeError(f"Plugin checkout mismatch: {name}")
            os.replace(stage, folder)
        results.append(f"changed: {name} {commit[:12]}")
    return results


def install_archive_tool(project_root: Path, platform: Platform, name: str) -> str:
    """Verify the binary against its release asset before running or exposing it."""
    entries = _manifest(project_root)["archiveTools"]
    if not isinstance(entries, dict) or not isinstance(entry := entries[name], dict):
        raise ValueError("Invalid editor tool release")
    archive = _archive(entry, platform, MAX_TOOL_ARCHIVE_BYTES)
    target = checked_directory(_data_home() / "setup-system/editor-tools" / archive.name) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    safe_file(target, os.getuid())
    with zipfile.ZipFile(archive) as bundle:
        matches = [
            item
            for item in bundle.infolist()
            if Path(item.filename).name == name and not item.is_dir()
        ]
        if len(matches) != 1 or matches[0].file_size > 128 * 1024**2:
            raise ValueError(f"Expected one bounded {name} executable")
        data = bundle.read(matches[0])
    if target.exists() and target.read_bytes() != data:
        raise ValueError("Existing parser tool differs from its release")
    status = "unchanged" if target.exists() else "changed"
    if not target.exists():
        atomic_write(target, data, 0o755)
    if str(entry["version"]) not in _run([str(target), "--version"], timeout=15):
        raise RuntimeError(f"{name} version verification failed")
    legacy = checked_directory(Path.home() / ".local/bin") / name
    if not legacy.is_symlink() and legacy.is_file() and legacy.read_bytes() == data:
        return f"{status}: {name}"
    expose_commands({name: target})
    return f"{status}: {name}"
