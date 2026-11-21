"""Install the chosen offline editor configuration and verify it in a disposable project."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from platformdirs import user_config_path

from setup_system.domain.editor_lock import EditorLock
from setup_system.domain.settings import UserSettings
from setup_system.infrastructure.catalog_repository import load_editor
from setup_system.infrastructure.editor_downloads import (
    install_archive_tool,
    install_neovim,
    install_plugins,
)
from setup_system.infrastructure.managed_files import apply_files
from setup_system.infrastructure.platform_facts import detect_platform
from setup_system.infrastructure.theme import render
from setup_system.resource_locator import ResourceLocator


def editor_files(root: Path, settings: UserSettings, manifest: EditorLock) -> dict[Path, bytes]:
    resources = ResourceLocator.for_root(root)
    source = resources.asset("nvim")
    config = user_config_path() / "nvim"
    files = {
        config / path.relative_to(source): render(
            resources.asset("nvim/" + path.relative_to(source).as_posix()).read_text(),
            root=root,
        ).encode()
        for path in source.rglob("*.lua")
    }
    lock = resources.resource("catalogs/typescript_vue.lock.json")
    languages = {
        "selected": settings.language_profiles,
        "node_environment": hashlib.sha256(lock.read_bytes()).hexdigest(),
    }
    files[config / "languages.json"] = (json.dumps(languages) + "\n").encode()
    native_lock = {
        name: {"branch": entry.branch, "commit": entry.commit}
        for name, entry in manifest.plugins.items()
        if name != "lazy"
    }
    files[config / "lazy-lock.json"] = (
        json.dumps(native_lock, sort_keys=True, indent=2) + "\n"
    ).encode()
    return files


def _headless(root: Path, settings: UserSettings, manifest: EditorLock, *, install: bool) -> None:
    parsers = tuple(
        dict.fromkeys(
            lang
            for profile in ("base", *settings.language_profiles)
            for lang in manifest.parsers[profile]
        )
    )
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
            SETUP_EDITOR_CHECK=str(ResourceLocator.for_root(root).asset("editor_check.lua")),
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
    platform = detect_platform(root=root)
    if not platform.implemented:
        raise ValueError(platform.reason)
    for name in ("git", "rg", "fzf", "wl-copy", "wl-paste"):
        if not shutil.which(name):
            raise RuntimeError(f"Editor prerequisite is missing: {name}")
    manifest = load_editor(root)
    if not set(settings.language_profiles) <= manifest.parsers.keys():
        raise ValueError("The editor release lacks a parser profile for the selected language")
    install_neovim(manifest.neovim, platform)
    install_plugins(manifest)
    if settings.language_profiles:
        install_archive_tool(manifest, platform, "tree-sitter")
    apply_files(editor_files(root, settings, manifest))
    _headless(root, settings, manifest, install=True)
    _headless(root, settings, manifest, install=False)
    config = user_config_path()
    environment_file = config / "environment.d/90-setup-system-editor.conf"
    apply_files(
        {
            config / "setup-system/editor.json": b'{"EDITOR":"nvim","VISUAL":"nvim"}\n',
            environment_file: b"EDITOR=nvim\nVISUAL=nvim\nGIT_EDITOR=nvim\n",
        }
    )
    os.environ.update(EDITOR="nvim", VISUAL="nvim", GIT_EDITOR="nvim")
