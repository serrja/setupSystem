"""Resolve a reviewed project template and build an immutable Copier request."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import JsonValue

from setup_system.domain.projects import TemplateMetadata
from setup_system.domain.templates import TemplatePlan, TemplateSource, snapshot_answers
from setup_system.infrastructure.project_layout import load_projects


def load_templates(root: Path) -> dict[str, TemplateSource]:
    """Use the existing catalog; never discover or accept arbitrary remote sources."""
    try:
        catalog = load_projects(root)
    except tomllib.TOMLDecodeError as error:
        raise ValueError("Invalid project/template catalog") from error
    return {
        row.template.alias: TemplateSource(
            project=row.id,
            url=f"https://github.com/{row.id}.git",
            metadata=TemplateMetadata(alias=row.template.alias),
        )
        for row in catalog.project
        if row.template is not None
    }


def plan_template(
    sources: dict[str, TemplateSource],
    *,
    template: str,
    version: str | None,
    destination: Path,
    operation: Literal["create", "update"] = "create",
    offline: bool = False,
    defaults: bool = False,
    data: dict[str, JsonValue] | None = None,
    cache_root: Path | None = None,
) -> TemplatePlan:
    """Resolve only a reviewed version; latest/main and arbitrary refs are rejected."""
    if template not in sources:
        raise ValueError("Choose a template alias from templates list")
    source = sources[template]
    selected = version if version is not None else source.metadata.recommended_version
    match = next((item for item in source.metadata.versions if item.version == selected), None)
    if match is None:
        raise ValueError("No reviewed version; choose a version from templates versions")
    if any(key.startswith("_") for key in data or {}):
        raise ValueError("Internal Copier answers cannot be supplied")
    cache = cache_root or Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return TemplatePlan(
        operation=operation,
        source=source,
        version=match,
        destination=destination.expanduser().absolute(),
        cache_root=cache / "setup-system/templates",
        offline=offline,
        defaults=defaults,
        data=snapshot_answers(data or {}),
    )
