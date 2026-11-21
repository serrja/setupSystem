"""Read reviewed selections from the exact release root supplied by the caller."""

from __future__ import annotations

import platform
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from pydantic import BaseModel, TypeAdapter

from setup_system.domain.catalog import (
    Component,
    ComponentCatalog,
    ComponentDefinition,
    ComponentID,
)
from setup_system.domain.cleanup import CleanupCandidate, CleanupCatalogDocument
from setup_system.domain.editor_lock import EditorLock
from setup_system.domain.happ import HappManifest, HappPin
from setup_system.domain.platforms import CapabilityPolicy
from setup_system.infrastructure.downloads import GITHUB_RELEASE_HOSTS, validate_https_url
from setup_system.resource_locator import ResourceLocator, read_toml

if TYPE_CHECKING:
    from setup_system.domain.projects import ProjectCatalog


ARTIFACT_ORIGINS = MappingProxyType(
    {
        "chatgpt": frozenset({"persistent.oaistatic.com"}),
        "obsidian": GITHUB_RELEASE_HOSTS,
    }
)


def load_components(
    root: Path | None = None, *, architecture: str | None = None
) -> ComponentCatalog:
    resources = ResourceLocator.for_root(root)
    definitions = TypeAdapter(dict[ComponentID, ComponentDefinition]).validate_python(
        read_toml(resources.manifest("applications.toml"))
    )
    policy = load_policy(resources.root)
    machine = policy.normalize_architecture(architecture or platform.machine())
    arch = policy.debian.apt_architectures.get(machine, machine)
    entries = []
    for name, row in definitions.items():
        if row.handler == "catalog_deb":
            if name not in ARTIFACT_ORIGINS:
                raise ValueError("No reviewed Debian artifact source for this component")
            for pin in row.artifacts.values():
                validate_https_url(pin.url, ARTIFACT_ORIGINS[name])
        artifact = next((pin for key, pin in row.artifacts.items() if key == arch), None)
        blocker = None
        if row.handler == "catalog_deb" and artifact is None:
            blocker = f"No reviewed {name} Debian artifact for {arch} in this release"
        entries.append(
            Component(
                id=name,
                kind=row.kind,
                title=row.title,
                handler=row.handler,
                packages=row.packages,
                blocker=blocker,
                artifact=artifact,
            )
        )
    return ComponentCatalog(entries=tuple(entries))


def load_projects(root: Path) -> ProjectCatalog:
    from setup_system.domain.projects import ProjectCatalog

    return read_contract(ResourceLocator.for_root(root).manifest("projects.toml"), ProjectCatalog)


def load_editor(root: Path) -> EditorLock:
    return read_contract(ResourceLocator.for_root(root).manifest("editor.toml"), EditorLock)


def load_cleanup(root: Path | None = None) -> Mapping[str, CleanupCandidate]:
    return read_contract(
        ResourceLocator.for_root(root).resource("catalogs/optional_cleanup.toml"),
        CleanupCatalogDocument,
    ).candidates


def read_contract[T: BaseModel](path: Path, model: type[T]) -> T:
    return model.model_validate(read_toml(path), by_alias=True, by_name=False)


def load_policy(root: Path | None = None) -> CapabilityPolicy:
    return read_contract(
        ResourceLocator.for_root(root).manifest("platforms.toml"), CapabilityPolicy
    )


def load_happ_pin(root: Path, architecture: str) -> HappPin:
    manifest = read_contract(ResourceLocator.for_root(root).manifest("happ.toml"), HappManifest)
    asset = manifest.linux.get(architecture)
    if asset is None:
        raise ValueError("Missing pinned Happ package for the target architecture")
    return HappPin(
        digest=asset.sha256,
        version=manifest.package_version,
        architecture=architecture,
        url=asset.url,
    )
