"""Prepare native template destinations and inspect the selected managers' local metadata."""

import hashlib
import json
import os
import re
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, TypedDict

from pydantic import Field

from setup_system.domain.auth_screen import AuthScreen
from setup_system.domain.contracts import ContractModel
from setup_system.infrastructure.receipt_repository import ownership
from setup_system.infrastructure.tool_paths import personal_path


class RenderFacts(TypedDict):
    home: str
    xdg: dict[str, str]


class RenderSelection(TypedDict, total=False):
    hyprland: bool
    desktop_theme: bool
    keyboard: str
    monitors: list[dict[str, object]]
    languages: list[str]
    theme: dict[str, bool | int | float]


class FilePreview(TypedDict):
    after: dict[str, list[str | int]]


class EditorSearch(ContractModel):
    exclude_globs: tuple[
        Annotated[
            str, Field(strict=True, max_length=512, pattern=r"^[^!\x00-\x1f\x7f][^\x00-\x1f\x7f]*$")
        ],
        ...,
    ] = Field(min_length=1, max_length=256)


def editor_globs_lua(search: str) -> str:
    """Render catalog globs as Lua strings for Neovim's native shell escaping."""
    patterns = EditorSearch.model_validate_json(search).exclude_globs
    return "{" + ", ".join(json.dumps(glob, ensure_ascii=False) for glob in patterns) + "}"


def editor_specs(
    files: str,
    info: RenderFacts,
    selected: list[str],
    settings: RenderSelection,
    catalogs: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    home = Path(info["home"])
    config = Path(info["xdg"]["config"]).relative_to(home)
    data = Path(info["xdg"]["data"]).relative_to(home)
    root = Path(files)
    rows = []

    def add(source: Path, dest: Path, mode: str = "0644", binary: Path | None = None) -> None:
        rows.append(
            {"source": str(source), "path": str(dest), "mode": mode, "binary": str(binary or "")}
        )

    base = settings.get("hyprland", False)
    theme = settings.get("desktop_theme", False)
    desktop = config / "hypr/setup-system"
    if base or theme:
        assets = tomllib.loads((root / "config/base/desktop.toml").read_text())
        managed = ownership().files if base and not theme else {}
        names = assets["theme"] + (assets["session"] if base else [])
        for name in names:
            source = root / "assets/hyprland" / name
            if source.is_symlink() or not source.is_file():
                raise ValueError(f"Missing or unsafe desktop release asset: {name}")
            content = source.read_text()
            if name == "panel.py":
                compile(content, str(source), "exec")
            if name == "start-hyprland.sh":
                add(source, data / "setup-system/bin/start-hyprland", "0700")
            elif name in assets["theme"] and str(home / desktop / name) in managed:
                dest = home / desktop / name
                personal_path(home, dest, regular=True)
                if (
                    dest.exists()
                    and hashlib.sha256(dest.read_bytes()).hexdigest() == managed[str(dest)]
                ):
                    continue
                add(source, desktop / name)
            else:
                add(source, desktop / name, "0755" if source.suffix in {".sh", ".py"} else "0644")
    if base:
        add(root / "ansible/templates/hyprland.lua", config / "hypr/hyprland.lua")
        add(
            root / "ansible/templates/hyprland-portals.conf",
            config / "xdg-desktop-portal/hyprland-portals.conf",
        )
        add(
            root / "assets/fonts/60-setup-system-fonts.conf",
            config / "fontconfig/conf.d/60-setup-system-fonts.conf",
        )
    if settings.get("keyboard") and base:
        add(root / "ansible/templates/hypr-keyboard.lua", desktop / "keyboard.lua")
    if settings.get("monitors"):
        add(root / "ansible/templates/hypr-monitors.lua", desktop / "monitors.lua")
    if "neovim" in selected:
        add(root / "ansible/templates/nvim-init.lua", config / "nvim/init.lua")
        add(
            root / "assets/nvim/colors/catppuccin_mocha_green.lua",
            config / "nvim/colors/catppuccin_mocha_green.lua",
        )
        for lua in ("keyMaps.lua", "options.lua", "project.lua"):
            add(root / "assets/nvim/lua/config" / lua, config / "nvim/lua/config" / lua)
        add(
            root / "ansible/templates/editor-env.conf",
            config / "environment.d/90-setup-system-editor.conf",
        )
    for tool, commands in catalogs.items():
        if tool not in selected:
            continue
        for command, path in commands.items():
            binary = home / data / "setup-system/tools" / path
            add(
                root / "ansible/templates/tool-wrapper.sh",
                Path(".local/bin") / command,
                "0755",
                binary,
            )
    return rows


def context(
    palette: dict[str, str], settings: RenderSelection, info: RenderFacts
) -> dict[str, object]:
    selected = settings.get("theme", {})
    colors = {
        k: v for k, v in palette.items() if isinstance(v, str) and re.fullmatch(r"#[a-f0-9]{6}", v)
    }
    values = dict(colors)
    for k, c in colors.items():
        values[k + "_hex"] = c[1:]
        values[k + "_rgb"] = ", ".join(str(int(c[i : i + 2], 16)) for i in (1, 3, 5))
    return dict(
        values,
        rounding=selected.get("rounding", 12),
        opacity=selected.get("opacity", 0.96),
        panel_height=30 if selected.get("compact_panel") else 36,
        effects="true" if selected.get("effects", True) else "false",
        keyboard=settings.get("keyboard"),
        monitors=settings.get("monitors", []),
        auth_screen=AuthScreen().tokens(colors),
        fonts={
            "family": "JetBrains Mono",
            "emoji_fallback": "Noto Color Emoji",
            "ui_size": 10,
            "terminal_size": 11,
            "panel_size": 12,
        },
        selected_lua="{" + ",".join(json.dumps(x) for x in settings.get("languages", [])) + "}",
        xdg={k: v.replace("'", "'\"'\"'") for k, v in info["xdg"].items()},
    )


def manager_paths(data: str, selected: list[str]) -> bool:
    root = Path(data) / "setup-system/tools"
    home = Path(os.environ["HOME"])
    if selected:
        for target in (
            root,
            root / "npm",
            root / "pipx",
            root / "bin",
            root / "npm/lib/node_modules",
            root / "npm/bin",
            root / "pipx/venvs",
        ):
            personal_path(home, target)
    return True


def metadata_version(data: str, manager: str, name: str) -> str | None:
    root = Path(data) / "setup-system/tools"
    path = (
        root / "npm/lib/node_modules" / name / "package.json"
        if manager == "npm"
        else root / "pipx/venvs" / name / "pipx_metadata.json"
    )
    personal_path(Path(os.environ["HOME"]), path, regular=True)
    if path.exists():
        record = json.loads(path.read_text())
        value = (
            record.get("version")
            if manager == "npm"
            else record.get("main_package", {}).get("package_version")
        )
        if value is not None and not isinstance(value, str):
            raise ValueError("Manager metadata contains an invalid version")
        return value if isinstance(value, str) else None
    return None


def missing_parsers(data: str, parsers: list[str]) -> list[str]:
    home = Path(os.environ["HOME"])
    root = Path(data) / "setup-system/editor-parsers/parser"
    missing = []
    for name in parsers:
        path = root / (name + ".so")
        personal_path(home, path, regular=True, max_bytes=None)
        if not path.is_file():
            missing.append(name)
    return missing


def editor_proof(
    info: RenderFacts,
    profiles: list[str],
    parsers: list[str],
    tools: dict[str, dict[str, str]],
    preview: FilePreview,
) -> dict[str, object]:
    prefix = Path(info["xdg"]["config"]).relative_to(info["home"]) / "nvim"
    return {
        "profiles": sorted(profiles),
        "parsers": sorted(parsers),
        "versions": {name: tool["version"] for name, tool in tools.items()},
        "files": {
            name: value
            for name, value in preview["after"].items()
            if Path(name).is_relative_to(prefix)
        },
    }


def stable_tag(rows: Iterable[dict[str, str]], major: int) -> str:
    """Choose the latest stable semantic version in the compatible upstream series."""
    choices = {}
    for row in rows:
        ref = row["ref"]
        match = re.fullmatch(r"refs/tags/v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", ref)
        if match and int(match[1]) == major:
            choices[tuple(map(int, match.groups()))] = ref
    if not choices:
        raise ValueError(f"No stable release found for the reviewed plugin series {major}")
    return choices[max(choices)]


class FilterModule:
    def filters(self) -> dict[str, object]:
        return {
            "editor_globs_lua": editor_globs_lua,
            "editor_proof": editor_proof,
            "stable_tag": stable_tag,
            "workspace_templates": editor_specs,
            "workspace_context": context,
            "managed_version": metadata_version,
            "manager_paths": manager_paths,
            "missing_parsers": missing_parsers,
        }
