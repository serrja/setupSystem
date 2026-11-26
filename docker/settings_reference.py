"""Render the offline settings reference from the models and packaged resources."""

import argparse
import json
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from setup_system.settings import ENV_PREFIX, Settings, default_database_url


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
    defaults = tomllib.loads((config / "application.toml").read_text())
    defaults.update(
        database_url=default_database_url(Path("/<XDG_STATE_HOME>")).get_secret_value(),
        database_busy_timeout_ms=Settings.model_fields["database_busy_timeout_ms"].default,
    )
    lines = [
        "# ⚙️ Settings reference",
        "",
        "The launcher reads packaged settings and explicit environment overrides.",
        "Each plugin ships a generated `settings_reference.md` beside its manifest and Help.",
        "",
        *table(Settings, defaults, ENV_PREFIX),
        "",
        "See [Configuration](configuration.md) for plugin settings storage and precedence.",
        "",
    ]
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
