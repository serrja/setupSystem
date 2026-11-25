"""Read known pre-plugin settings as data without invoking a workstation action."""

from pathlib import Path

import tomlkit
from pydantic import ConfigDict, Field, JsonValue, TypeAdapter

from setup_system.configuration.legacy_models import BaseOptions, Section, WorkspaceOptions
from setup_system.infrastructure.user_storage import FileSnapshot, private_directory
from setup_system_sdk import ActionId, Identifier, Inputs, Values

_VALUES = TypeAdapter(dict[str, JsonValue], config=ConfigDict(hide_input_in_errors=True))
_LEGACY = {"base": "system-intentions.toml", "workspace": "settings.toml"}
_V1_APPS = {"neovim", "firefox", "codex", "chatgpt", "happ", "developer_tools"}


def _decode(section: Section, raw: bytes | None, legacy: bool) -> dict[str, JsonValue]:
    values = (
        _VALUES.validate_python(tomlkit.parse(raw.decode()).unwrap(), strict=True)
        if raw is not None
        else {}
    )
    version = values.pop("schema_version", 1)
    if type(version) is not int or version not in (
        (1, 2) if legacy and section == "workspace" else (1,)
    ):
        raise ValueError("Unknown settings schema; review the original file")
    if section == "base":
        return values
    if legacy:
        if version == 1:
            allowed = _V1_APPS | {
                "theme",
                "language_profiles",
                "optional_apps",
                "cleanup",
                "cleanup_mode",
            }
            if values.keys() - allowed or any(
                type(values[k]) is not bool for k in values.keys() & _V1_APPS
            ):
                raise ValueError("Invalid version 1 application selections")
            values["apps"] = [key for key in sorted(_V1_APPS) if values.pop(key, False)]
        apps = values.get("apps", [])
        optional = values.pop("optional_apps", [])
        if not isinstance(apps, list) or not isinstance(optional, list) or "languages" in values:
            raise ValueError("Invalid legacy workspace selections")
        values["apps"] = apps + optional
        values["languages"] = values.pop("language_profiles", [])
        theme = values.get("theme", {})
        if (
            not isinstance(theme, dict)
            or theme.pop("name", "catppuccin_mocha_green") != "catppuccin_mocha_green"
        ):
            raise ValueError("Unknown legacy theme")
    return values


def decode(path: Path) -> BaseOptions | WorkspaceOptions:
    names = {
        "base.toml": "base",
        "system-intentions.toml": "base",
        "workspace.toml": "workspace",
        "settings.toml": "workspace",
    }
    if path.name not in names or path.is_symlink() or path.stat().st_size > 65536:
        raise ValueError("Expected a bounded, known pre-plugin settings file")
    section: Section = "base" if names[path.name] == "base" else "workspace"
    with private_directory(path.parent, create=False) as fd:
        data = FileSnapshot.read(fd, path.name, limit=65536).data
    values = _decode(section, data, path.name in _LEGACY.values())
    return (BaseOptions if section == "base" else WorkspaceOptions).model_validate(values)


class LegacySelection(Inputs):
    plugin: Identifier
    action: ActionId
    inputs: Values = Field(default_factory=dict)


class Imported(Inputs):
    settings: dict[Identifier, Values] = Field(default_factory=dict)
    actions: list[LegacySelection] = Field(default_factory=list)


def translate(path: Path) -> Imported:
    """Project only documented legacy intentions; retain their source for manual recovery."""
    old = decode(path)
    settings: dict[str, Values] = {}
    actions: list[LegacySelection] = []

    def add(plugin: str, action: str, **values: JsonValue) -> None:
        row = LegacySelection(plugin=plugin, action=action, inputs=values)
        if row not in actions:
            actions.append(row)

    if isinstance(old, BaseOptions):
        for enabled, plugin in (
            (old.hyprland, "hyprland"),
            (old.graphical_login, "greeter"),
            (old.plymouth, "plymouth"),
        ):
            if enabled:
                add(plugin, "install")
        if old.timezone:
            add("regional", "timezone", value=old.timezone)
        if old.locale:
            add("regional", "locale", value=old.locale)
        if old.keyboard:
            add("regional", "keyboard", layouts=[v for v in old.keyboard.split(",")])
    else:
        for app in old.apps:
            for plugin in ("git", "zsh") if app == "developer_tools" else (app,):
                add(plugin, "install")
        for language in old.languages:
            add(language, "install")
        if old.languages:
            settings["neovim"] = {"languages": list(old.languages)}
        if old.desktop_theme or "theme" in old.model_fields_set:
            settings["hyprland"] = {"effects": old.theme.effects, "rounding": old.theme.rounding}
            settings["waybar"] = {
                "compact": old.theme.compact_panel,
                "opacity": old.theme.opacity,
                "rounding": old.theme.rounding,
            }
        for monitor in old.monitors:
            add("hyprland", "save-monitor", **monitor.model_dump(mode="json"))
        if old.cleanup:
            add("cleanup", old.cleanup_mode.value, packages=list(old.cleanup))
        if old.project:
            add("projects", "open-editor", project=old.project)
    return Imported(settings=settings, actions=actions)
