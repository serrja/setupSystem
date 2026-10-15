"""Install pinned terminal tools without executing installers at shell or editor startup."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from setup_system.planning import Platform


def _manifest(projectRoot: Path) -> dict[str, object]:
    return tomllib.loads((projectRoot / "manifests/terminalDev.toml").read_text())


def _dataHome() -> Path:
    home = Path.home().resolve()
    target = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")).expanduser().absolute()
    if not target.is_relative_to(home) or any(
        parent.is_symlink() for parent in target.parents if parent != Path("/")
    ):
        raise ValueError("XDG_DATA_HOME must be inside the current home without symlink parents")
    return target


def _run(argv: list[str], *, cwd: Path | None = None, timeout: int = 180) -> str:
    safeEnv = {
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
    safeEnv["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(
        argv, cwd=cwd, env=safeEnv, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {argv[0]} {argv[1] if len(argv) > 1 else ''}"
        )
    return result.stdout.strip()


def _archiveKey(platform: Platform) -> str:
    if platform.distribution in {"debian", "ubuntu"} and platform.architecture == "x86_64":
        return "linux_x86_64"
    if platform.distribution == "macos" and platform.architecture in {"arm64", "x86_64"}:
        return f"macos_{platform.architecture}"
    raise ValueError(platform.reason)


def installNeovim(projectRoot: Path, platform: Platform) -> str:
    """Install one official, digest-checked stable archive in user data."""
    if not platform.implemented:
        raise ValueError(platform.reason)
    config = _manifest(projectRoot)["neovim"]
    if not isinstance(config, dict):
        raise ValueError("Invalid Neovim manifest")
    key = _archiveKey(platform)
    version = str(config["version"])
    target = _dataHome() / "setup-system/neovim" / version
    binary = target / "bin/nvim"
    link = Path.home() / ".local/bin/nvim"
    if link.is_symlink() and link.resolve() != binary:
        raise ValueError("Existing nvim link has another owner")
    if link.exists() and not link.is_symlink():
        raise ValueError("Existing ~/.local/bin/nvim has another owner")
    if binary.is_file():
        observed = _run([str(binary), "--version"], timeout=15).splitlines()[0]
        if observed != f"NVIM v{version}":
            raise RuntimeError("Existing managed Neovim has an unexpected version")
        status = "unchanged"
    else:
        url = str(config[f"{key}_url"])
        digest = str(config[f"{key}_sha256"])
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid Neovim digest")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".neovim-", dir=target.parent) as temporary:
            stage = Path(temporary)
            archive = stage / "neovim.tar.gz"
            sha = hashlib.sha256()
            size = 0
            with urlopen(url, timeout=30) as response, archive.open("wb") as output:
                finalUrl = urlsplit(response.geturl())
                if finalUrl.scheme != "https" or finalUrl.hostname not in {
                    "github.com",
                    "release-assets.githubusercontent.com",
                    "objects.githubusercontent.com",
                }:
                    raise RuntimeError("Unexpected Neovim release redirect")
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > 120 * 1024 * 1024:
                        raise RuntimeError("Neovim archive exceeds size limit")
                    sha.update(chunk)
                    output.write(chunk)
            if sha.hexdigest() != digest:
                raise RuntimeError("Neovim archive digest mismatch")
            archiveOs = "linux" if platform.distribution in {"debian", "ubuntu"} else "macos"
            expectedTop = f"nvim-{archiveOs}-{platform.architecture}"
            with tarfile.open(archive, "r:gz") as bundle:
                for member in bundle.getmembers():
                    parts = Path(member.name).parts
                    if not parts or parts[0] != expectedTop or ".." in parts:
                        raise RuntimeError("Unsafe Neovim archive path")
                bundle.extractall(stage, filter="data")
            staged = stage / expectedTop
            if not (staged / "bin/nvim").is_file():
                raise RuntimeError("Neovim archive has no binary")
            os.replace(staged, target)
        observed = _run([str(binary), "--version"], timeout=15).splitlines()[0]
        if observed != f"NVIM v{version}":
            raise RuntimeError("Installed Neovim failed version verification")
        status = "changed"
    if not link.is_symlink():
        link.parent.mkdir(parents=True, exist_ok=True)
        candidate = link.parent / f".nvim-{os.getpid()}"
        candidate.symlink_to(binary)
        os.replace(candidate, link)
    return f"{status}: Neovim {version} at {binary}"


def installPlugins(projectRoot: Path) -> list[str]:
    """Check out exact public revisions into Neovim's standard user data path."""
    plugins = _manifest(projectRoot)["plugins"]
    if not isinstance(plugins, dict):
        raise ValueError("Invalid plugin manifest")
    destination = _dataHome() / "nvim/lazy"
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
        folderName = "lazy.nvim" if name == "lazy" else name
        folder = destination / folderName
        if folder.is_symlink():
            raise ValueError(f"Plugin path is a symlink: {folder}")
        if folder.exists():
            actual = _run(["git", "-C", str(folder), "rev-parse", "HEAD"], timeout=15)
            if actual != commit:
                raise ValueError(f"Existing plugin differs from pinned revision: {name}")
            results.append(f"unchanged: {name} {commit[:12]}")
            continue
        with tempfile.TemporaryDirectory(prefix=f".{folderName}-", dir=destination) as temporary:
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


def _verifiedUserBinary(projectRoot: Path, name: str) -> str:
    binaries = _manifest(projectRoot)["userBinary"]
    if not isinstance(binaries, dict):
        raise ValueError("Invalid user binary manifest")
    entry = binaries[name]
    if not isinstance(entry, dict):
        raise ValueError("Invalid binary entry")
    target = Path.home() / ".local/bin" / name
    version = str(entry["version"])
    if target.exists() or target.is_symlink():
        observed = _run([str(target), "--version"], timeout=10).splitlines()[0]
        if version not in observed:
            raise ValueError(f"Existing user binary has another version: {name}")
        return f"unchanged: {name} {version}"
    digest = str(entry["sha256"])
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid binary digest")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{name}-", dir=target.parent) as temporary:
        archive = Path(temporary) / "archive.tar.gz"
        sha = hashlib.sha256()
        size = 0
        with urlopen(str(entry["url"]), timeout=30) as response, archive.open("wb") as output:
            finalUrl = urlsplit(response.geturl())
            if finalUrl.scheme != "https" or finalUrl.hostname not in {
                "github.com",
                "release-assets.githubusercontent.com",
                "objects.githubusercontent.com",
            }:
                raise RuntimeError("Unexpected binary release redirect")
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 100 * 1024 * 1024:
                    raise RuntimeError("Binary archive exceeds size limit")
                sha.update(chunk)
                output.write(chunk)
        if sha.hexdigest() != digest:
            raise RuntimeError("Binary archive digest mismatch")
        with tarfile.open(archive, "r:gz") as bundle:
            matches = [
                member
                for member in bundle.getmembers()
                if member.isfile() and Path(member.name).name == name
            ]
            if len(matches) != 1:
                raise RuntimeError(f"Archive does not contain one {name} binary")
            stream = bundle.extractfile(matches[0])
            if stream is None:
                raise RuntimeError("Archive binary could not be read")
            candidate = Path(temporary) / name
            with candidate.open("wb") as output:
                shutil.copyfileobj(stream, output)
            candidate.chmod(0o755)
            os.replace(candidate, target)
    observed = _run([str(target), "--version"], timeout=10).splitlines()[0]
    if version not in observed:
        raise RuntimeError(f"Installed binary version mismatch: {name}")
    return f"changed: {name} {version}"


def installArchiveTool(projectRoot: Path, platform: Platform, name: str) -> str:
    """Install one hash-checked upstream zip with no runtime update hook."""
    tools = _manifest(projectRoot)["archiveTools"]
    if not isinstance(tools, dict):
        raise ValueError("Invalid archive tool manifest")
    entry = tools[name]
    if not isinstance(entry, dict):
        raise ValueError("Invalid archive tool entry")
    target = Path.home() / ".local/bin" / name
    version = str(entry["version"])
    if target.exists() or target.is_symlink():
        observed = _run([str(target), "--version"], timeout=10).splitlines()[0]
        if version not in observed:
            raise ValueError(f"Existing archive tool has another version: {name}")
        return f"unchanged: {name} {version}"
    key = _archiveKey(platform)
    digest = str(entry[f"{key}_sha256"])
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid archive tool digest")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{name}-", dir=target.parent) as temporary:
        archive = Path(temporary) / "archive.zip"
        sha = hashlib.sha256()
        size = 0
        with (
            urlopen(str(entry[f"{key}_url"]), timeout=30) as response,
            archive.open("wb") as output,
        ):
            finalUrl = urlsplit(response.geturl())
            if finalUrl.scheme != "https" or finalUrl.hostname not in {
                "github.com",
                "release-assets.githubusercontent.com",
                "objects.githubusercontent.com",
            }:
                raise RuntimeError("Unexpected archive tool redirect")
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 100 * 1024 * 1024:
                    raise RuntimeError("Archive tool exceeds size limit")
                sha.update(chunk)
                output.write(chunk)
        if sha.hexdigest() != digest:
            raise RuntimeError("Archive tool digest mismatch")
        with zipfile.ZipFile(archive) as bundle:
            matches = [
                item
                for item in bundle.infolist()
                if not item.is_dir() and Path(item.filename).name == name
            ]
            if len(matches) != 1:
                raise RuntimeError(f"Archive does not contain one {name} binary")
            candidate = Path(temporary) / name
            with bundle.open(matches[0]) as source, candidate.open("wb") as output:
                shutil.copyfileobj(source, output)
            candidate.chmod(0o755)
            os.replace(candidate, target)
    observed = _run([str(target), "--version"], timeout=10).splitlines()[0]
    if version not in observed:
        raise RuntimeError(f"Archive tool version mismatch: {name}")
    return f"changed: {name} {version}"


def installLanguageTools(projectRoot: Path) -> list[str]:
    """Install pinned Python editor tools with uv, separate from project environments."""
    versions = _manifest(projectRoot)["languageTools"]
    if not isinstance(versions, dict):
        raise ValueError("Invalid language tools manifest")
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required for editor language tools")
    results: list[str] = []
    for name, versionValue in versions.items():
        version = str(versionValue)
        executable = Path.home() / ".local/bin" / name
        if executable.exists() or executable.is_symlink():
            observed = _run([str(executable), "--version"], timeout=15)
            if version not in observed:
                raise ValueError(f"Existing editor tool has another version: {name}")
            results.append(f"unchanged: {name} {version}")
            continue
        _run([uv, "tool", "install", "--python", "3.14", f"{name}=={version}"], timeout=300)
        if not executable.is_file():
            raise RuntimeError(f"uv did not expose {name}")
        results.append(f"changed: {name} {version}")
    return results


def installParsers(projectRoot: Path) -> str:
    """Build only declared Tree-sitter parsers during explicit apply."""
    settings = _manifest(projectRoot)["parsers"]
    if not isinstance(settings, dict) or not isinstance(settings.get("languages"), list):
        raise ValueError("Invalid parser manifest")
    languages = settings["languages"]
    if not all(
        isinstance(language, str) and re.fullmatch(r"[a-z_]+", language) for language in languages
    ):
        raise ValueError("Invalid parser language")
    parserRoot = _dataHome() / "nvim/site/parser"
    missing = [language for language in languages if not (parserRoot / f"{language}.so").is_file()]
    if not missing:
        return f"unchanged: {len(languages)} Tree-sitter parsers"
    if shutil.which("cc") is None:
        return f"blocked: C compiler unavailable for parsers {', '.join(missing)}"
    editor = Path.home() / ".local/bin/nvim"
    luaList = "{" + ",".join(f'"{language}"' for language in missing) + "}"
    command = f"lua require('nvim-treesitter').install({luaList}):wait(300000)"
    _run([str(editor), "--headless", f"+{command}", "+qa!"], timeout=360)
    remaining = [language for language in missing if not (parserRoot / f"{language}.so").is_file()]
    if remaining:
        raise RuntimeError(f"Parser installation incomplete: {', '.join(remaining)}")
    return f"changed: parsers {', '.join(missing)}"


def installSystemTools(projectRoot: Path, platform: Platform) -> tuple[str, bool]:
    """Install only missing terminal packages from the platform's established manager."""
    tools = _manifest(projectRoot)["tools"]
    if not isinstance(tools, dict):
        raise ValueError("Invalid tool manifest")
    if platform.distribution in {"debian", "ubuntu"}:
        commands = {
            "zsh": "zsh",
            "ripgrep": "rg",
            "fzf": "fzf",
            "fd-find": "fdfind",
            "tmux": "tmux",
            "clangd": "clangd",
        }
        aptKey = f"{platform.distribution}_apt"
        missing = [name for name in tools[aptKey] if shutil.which(commands[name]) is None]
        if not missing:
            return f"unchanged: {platform.distribution} terminal packages", False
        sudo = shutil.which("sudo")
        if sudo:
            probe = subprocess.run(
                [sudo, "-n", "true"], capture_output=True, timeout=10, check=False
            )
            if probe.returncode == 0:
                _run([sudo, "-n", "apt-get", "install", "-y", *missing], timeout=900)
                return f"changed: {platform.distribution} packages {', '.join(missing)}", False
        installed = [_verifiedUserBinary(projectRoot, name) for name in ("rg", "fzf")]
        blocked = [name for name in missing if name not in {"ripgrep", "fzf"}]
        if blocked:
            installed.append(f"blocked: targeted sudo unavailable for {', '.join(blocked)}")
        return "; ".join(installed), bool(blocked)
    if platform.distribution == "macos":
        brew = shutil.which("brew")
        if not brew:
            return (
                "blocked: Homebrew absent; install official Homebrew and Python before bootstrap",
                True,
            )
        prefix = _run([brew, "--prefix"], timeout=15)
        expected = "/opt/homebrew" if platform.architecture == "arm64" else "/usr/local"
        if prefix != expected:
            return f"blocked: Homebrew prefix {prefix} differs from expected {expected}", True
        missing = [name for name in tools["macos_brew"] if not _installedBrew(brew, name)]
        if not missing:
            return "unchanged: macOS terminal packages", False
        _run([brew, "install", *missing], timeout=1200)
        return f"changed: macOS packages {', '.join(missing)}", False
    raise ValueError(platform.reason)


def _installedBrew(brew: str, formula: str) -> bool:
    result = subprocess.run(
        [brew, "list", "--versions", formula],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return result.returncode == 0 and bool(result.stdout.strip())
