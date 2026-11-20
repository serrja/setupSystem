"""Install the chosen offline editor configuration and verify it in a disposable project."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

from platformdirs import user_config_path

from setup_system.domain.settings import UserSettings
from setup_system.infrastructure.editor_downloads import (
    install_archive_tool,
    install_neovim,
    install_plugins,
)
from setup_system.infrastructure.theme import RESOURCE_ROOT, render
from setup_system.infrastructure.user_files import apply_files
from setup_system.planning import detect_platform


def editor_files(root: Path, settings: UserSettings) -> dict[Path, bytes]:
    source = root / "assets/nvim"
    config = user_config_path() / "nvim"
    files = {
        config / path.relative_to(source): render(path.read_text()).encode()
        for path in source.rglob("*.lua")
    }
    lock = RESOURCE_ROOT / "catalogs/typescript_vue.lock.json"
    languages = {
        "selected": settings.language_profiles,
        "node_environment": hashlib.sha256(lock.read_bytes()).hexdigest(),
    }
    files[config / "languages.json"] = (json.dumps(languages) + "\n").encode()
    manifest = tomllib.loads((root / "manifests/editor.toml").read_text())
    native_lock = {
        name: {"branch": entry["branch"], "commit": entry["commit"]}
        for name, entry in manifest["plugins"].items()
        if name != "lazy"
    }
    files[config / "lazy-lock.json"] = (
        json.dumps(native_lock, sort_keys=True, indent=2) + "\n"
    ).encode()
    return files


def _headless(root: Path, settings: UserSettings, *, install: bool) -> None:
    manifest = tomllib.loads((root / "manifests/editor.toml").read_text())
    parsers = tuple(
        dict.fromkeys(
            lang
            for profile in ("base", *settings.language_profiles)
            for lang in manifest["parsers"][profile]
        )
    )
    if not all(re.fullmatch(r"[a-z_]+", lang) for lang in parsers):
        raise ValueError("Invalid Tree-sitter parser in editor lock")
    binary = Path.home() / ".local/bin/nvim"
    config = user_config_path() / "nvim"
    with tempfile.TemporaryDirectory(prefix="setup-system-editor-") as temporary:
        project = Path(temporary)
        (project / "pyproject.toml").write_text("")
        (project / "tsconfig.json").write_text("{}")
        (project / ".prettierrc").write_text("{}")
        (project / "pubspec.yaml").write_text("name: editor_check\n")
        (project / "CMakeLists.txt").touch()
        environment = {
            key: value
            for key, value in os.environ.items()
            if key
            in {
                "HOME",
                "PATH",
                "LANG",
                "LC_ALL",
                "TMPDIR",
                "XDG_CONFIG_HOME",
                "XDG_DATA_HOME",
                "XDG_STATE_HOME",
                "XDG_CACHE_HOME",
                "WAYLAND_DISPLAY",
                "XDG_RUNTIME_DIR",
                "SSL_CERT_FILE",
            }
        }
        environment.update(
            PATH=str(binary.parent) + os.pathsep + environment.get("PATH", os.defpath),
            SETUP_EDITOR_CHECK=str(root / "assets/editor_check.lua"),
            SETUP_EDITOR_PARSERS=json.dumps(parsers),
            SETUP_EDITOR_PROFILES=json.dumps(settings.language_profiles),
            SETUP_EDITOR_INSTALL="1" if install else "0",
        )
        result = subprocess.run(
            [
                str(binary),
                "--headless",
                "-n",
                "-i",
                "NONE",
                "-u",
                str(config / "init.lua"),
                "+lua local ok,e=pcall(dofile,vim.env.SETUP_EDITOR_CHECK); "
                "if not ok then io.stderr:write(tostring(e)); vim.cmd('cquit 1') end",
                "+qa!",
            ],
            cwd=project,
            env=environment,
            capture_output=True,
            text=True,
            timeout=420,
        )
        if result.returncode or "setup-system editor verified" not in result.stdout:
            detail = result.stderr.strip().splitlines()[-1:] or ["no verification marker"]
            raise RuntimeError("Headless editor verification failed: " + detail[0][:350])


def apply_editor(root: Path, settings: UserSettings) -> None:
    if os.geteuid() == 0:
        raise ValueError("Neovim must be configured by the target user")
    platform = detect_platform()
    if not platform.implemented:
        raise ValueError(platform.reason)
    for name in ("git", "rg", "fzf", "wl-copy", "wl-paste"):
        if not shutil.which(name):
            raise RuntimeError(f"Editor prerequisite is missing: {name}")
    install_neovim(root, platform)
    install_plugins(root)
    if settings.language_profiles:
        install_archive_tool(root, platform, "tree-sitter")
    apply_files(editor_files(root, settings))
    _headless(root, settings, install=True)
    _headless(root, settings, install=False)
    config = user_config_path()
    environment_file = config / "environment.d/90-setup-system-editor.conf"
    apply_files(
        {
            config / "setup-system/editor.json": b'{"EDITOR":"nvim","VISUAL":"nvim"}\n',
            environment_file: b"EDITOR=nvim\nVISUAL=nvim\nGIT_EDITOR=nvim\n",
        }
    )
    os.environ.update(EDITOR="nvim", VISUAL="nvim", GIT_EDITOR="nvim")
