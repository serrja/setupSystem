"""Render the offline settings reference from the models and packaged resources."""

import argparse
import json
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from setup_system.configuration import APPLICATION_ENV_PREFIX, ApplicationOptions
from setup_system.domain.auth_screen import DesktopStyle
from setup_system.domain.displays import MonitorLayout
from setup_system.domain.intentions import BaseOptions, Theme, WorkspaceOptions


def type_text(field: dict[str, Any], definitions: dict[str, Any]) -> str:
    if "anyOf" in field:
        return " or ".join(type_text(item, definitions) for item in field["anyOf"])
    if "$ref" in field:
        name = str(field["$ref"]).split("/")[-1]
        if values := definitions[name].get("enum"):
            return ", ".join(map(str, values))
        return name
    value = str(field.get("type", "value"))
    if value == "array":
        value = "list of " + type_text(field["items"], definitions)
    parts = [value]
    for attr, label in (
        ("minimum", "min"),
        ("maximum", "max"),
        ("maxItems", "max items"),
        ("minLength", "min length"),
        ("maxLength", "max length"),
    ):
        if attr in field:
            parts.append(f"{label} {field[attr]}")
    if pattern := field.get("pattern"):
        parts.append("`" + str(pattern).replace("|", r"\|") + "`")
    return ", ".join(parts)


def table(model: type[BaseModel], defaults: dict[str, Any], prefix: str = "") -> list[str]:
    rows = ["| Field | Type and limits | Default |", "| --- | --- | --- |"]
    schema = model.model_json_schema()
    for key, field in schema["properties"].items():
        constraint = type_text(field, schema.get("$defs", {}))
        value = defaults.get(key)
        if key not in defaults:
            shown = "required"
        elif isinstance(value, dict):
            shown = "see nested fields"
        else:
            shown = json.dumps(value) if value is not None else "unset"
        name = prefix + key.upper() if prefix else key
        rows.append(f"| `{name}` | {constraint} | `{shown}` |")
    return rows


def reference(root: Path) -> str:
    config = root / "config"
    app = ApplicationOptions.model_validate(
        tomllib.loads((config / "application.toml").read_text())
    )
    desktop = DesktopStyle.model_validate(
        tomllib.loads((config / "shared/desktop.toml").read_text())
    )
    sections: list[tuple[str, str, type[BaseModel], dict[str, Any], str]] = [
        (
            "Application environment",
            "The normal-user launcher accepts the following environment variables. They "
            "override the numeric values in the packaged application.toml. Textual uses the "
            "suggestion settings and its Make runner uses the timeouts.",
            ApplicationOptions,
            app.model_dump(),
            APPLICATION_ENV_PREFIX,
        ),
        (
            "Base system",
            "The Base form, Preview and installed system play validate BaseOptions. The "
            "model defines the initial state. An explicitly selected packaged profile, "
            "saved base.toml and confirmed form values take increasing precedence.",
            BaseOptions,
            BaseOptions.model_validate({}).model_dump(),
            "",
        ),
        (
            "Working environment",
            "The Workspace form and its plays validate WorkspaceOptions. The profile, saved "
            "workspace.toml and confirmed values follow the same precedence. Cleanup "
            "requires a separate preview.",
            WorkspaceOptions,
            WorkspaceOptions.model_validate({}).model_dump(),
            "",
        ),
        (
            "Workspace theme",
            "The theme table in workspace.toml supplies the desktop renderers and uses "
            "these model defaults for missing fields.",
            Theme,
            Theme().model_dump(),
            "",
        ),
        (
            "Monitor layout",
            "Each entry in workspace.toml monitors identifies a connected output and its "
            "advertised mode. The compositor supplies the observation used to validate "
            "Preview and Apply.",
            MonitorLayout,
            {
                key: field.default
                for key, field in MonitorLayout.model_fields.items()
                if not field.is_required()
            },
            "",
        ),
        (
            "Packaged desktop style",
            "The packaged shared/desktop.toml supplies Waybar and native renderers. The "
            "installed package owns these values.",
            DesktopStyle,
            desktop.model_dump(),
            "",
        ),
        (
            "Packaged desktop fonts",
            "Foot, Mako, Waybar and fontconfig consume the fonts table.",
            type(desktop.fonts),
            desktop.fonts.model_dump(),
            "",
        ),
        (
            "Packaged authentication style",
            "The greetd greeter and hyprlock consume the auth_screen table with color roles "
            "from shared/palette.toml.",
            type(desktop.auth_screen),
            desktop.auth_screen.model_dump(),
            "",
        ),
    ]
    lines = [
        "# Settings reference",
        "",
        "Generated from the package resources and validation models. Run the reference "
        "check after changing a field. [Configuration](configuration.md) · "
        "[Development](development.md)",
        "",
    ]
    for title, description, model, defaults, prefix in sections:
        lines.extend([f"## {title}", "", description, "", *table(model, defaults, prefix), ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    target = root / "docs/settings_map.md"
    generated = reference(root)
    if args.write:
        target.write_text(generated)
    elif target.read_text() != generated:
        parser.exit(1, "Update the settings reference with --write.\n")


if __name__ == "__main__":
    main()
