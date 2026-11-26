"""Regenerate trusted source manifests and offline field references in the build tree."""

import argparse
import importlib
import importlib.util
import json
import sys
import tomllib
from pathlib import Path

from setup_system_sdk import PLUGIN_GROUP
from setup_system_sdk.enums.ui import StorageScope
from setup_system_sdk.plugin import Backend
from setup_system_sdk.schemas.common import Values
from setup_system_sdk.schemas.forms import form_schema
from setup_system_sdk.schemas.manifest import Manifest


def reference(manifest: Manifest) -> str:
    lines = [
        "# ⚙️ " + manifest.name + " settings reference",
        "",
        "Generated from the plugin's Pydantic settings and action inputs.",
        "",
    ]
    for name, schema in [
        ("Settings", manifest.settings_schema),
        *((action.label, manifest.input_schemas[action.id]) for action in manifest.actions),
    ]:
        lines.extend([("## ⚙️ " if name == "Settings" else "## 🧩 ") + name, ""])
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError("Expected object properties")
        if not properties:
            lines.extend(["This operation has no editable parameters.", ""])
            continue
        lines.extend(
            ["| Field | Description | Default | Constraints |", "| --- | --- | --- | --- |"]
        )
        for key, field in properties.items():
            if not isinstance(field, dict):
                raise ValueError("Unknown field description")
            detail = (
                str(field.get("description", field.get("title", key)))
                .replace("|", r"\|")
                .replace("\n", " ")
            )
            constraints = {
                k: v
                for k, v in field.items()
                if k
                in (
                    "format",
                    "enum",
                    "minimum",
                    "maximum",
                    "pattern",
                    "minLength",
                    "maxLength",
                    "minItems",
                    "maxItems",
                    "$ref",
                )
            }
            default = (
                json.dumps(field["default"], ensure_ascii=False)
                if "default" in field
                else "required"
            )
            limits = (
                json.dumps(constraints, ensure_ascii=False).replace("|", r"\|")
                if constraints
                else "—"
            )
            lines.append(f"| `{key}` | {detail} | `{default}` | `{limits}` |")
        lines.append("")
    return "\n".join(lines)


def update(project: Path, write: bool) -> list[Path]:
    package_metadata = tomllib.loads(project.read_text())
    metadata = package_metadata["project"]
    entries = metadata["entry-points"][PLUGIN_GROUP]
    ((plugin, entry),) = entries.items()
    module, _, name = entry.partition(":")
    package = module.split(".")[0]
    folder = project.parent / "src" / package
    spec = importlib.util.spec_from_file_location(
        package, folder / "__init__.py", submodule_search_locations=[str(folder)]
    )
    if not spec or not spec.loader:
        raise ValueError("Cannot load the reviewed source model")
    imported = importlib.util.module_from_spec(spec)
    sys.modules[package] = imported
    spec.loader.exec_module(imported)
    strategy = getattr(importlib.import_module(module), name)
    if not isinstance(strategy, type) or not issubclass(strategy, Backend):
        raise ValueError("Invalid backend")
    backend = strategy()
    path = folder / "manifest.json"
    data: Values = package_metadata["tool"]["setup-system"]["manifest"].copy()
    data.update(
        provides=[
            {
                "id": key,
                "version": backend.contract_versions.get(key, "1"),
                "request_schema": provider.request_model.model_json_schema(),
            }
            for key, provider in backend.providers.items()
        ],
        id=plugin,
        entry_point=entry,
        version=metadata["version"],
        environment_fields=dict(backend.settings_model.environment_fields),
        settings_version=backend.settings_model.schema_version,
        settings_schema=form_schema(backend.settings_model, StorageScope.SETTINGS),
        input_schemas={
            key: form_schema(command.schema, StorageScope.ACTION)
            for key, command in backend.commands().items()
        },
    )
    manifest = Manifest.model_validate(data)
    files = {
        path: json.dumps(manifest.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        folder / "settings_reference.md": reference(manifest),
    }
    changed = []
    for file, content in files.items():
        if not file.exists() or file.read_text() != content:
            changed.append(file)
            if write:
                file.write_text(content)
    return changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("projects", type=Path, nargs="*")
    args = parser.parse_args()
    paths = args.projects or sorted(
        (Path(__file__).resolve().parents[1] / "plugins").glob("*/pyproject.toml")
    )
    changed = [file for project in paths for file in update(project, args.write)]
    if changed and not args.write:
        raise SystemExit("Stale generated metadata: " + ", ".join(str(p) for p in changed))


if __name__ == "__main__":
    main()
