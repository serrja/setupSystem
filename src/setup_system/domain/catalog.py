"""Release-owned selections shared by the interface, settings and privileged planner."""

from __future__ import annotations

import platform
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from setup_system.capabilities import load_policy
from setup_system.entrypoint import resource_root

if TYPE_CHECKING:
    from setup_system.domain.settings import UserSettings

HANDLERS = frozenset(
    {"apt", "happ", "neovim", "firefox", "codex", "catalog_deb", "python_tools", "language"}
)


@dataclass(frozen=True)
class Component:
    id: str
    kind: str
    title: str
    handler: str
    packages: tuple[str, ...]
    blocker: str | None = None


def components(kind: str | None = None, *, root: Path | None = None) -> tuple[Component, ...]:
    root = root or resource_root()
    data = tomllib.loads((root / "manifests/applications.toml").read_text())
    policy = load_policy(root)
    machine = policy.normalize_architecture(platform.machine())
    arch = policy.debian.apt_architectures.get(machine, machine)
    result = []
    for name, row in data.items():
        handler = row["handler"]
        group = row["kind"]
        packages = tuple(row.get("packages", []))
        if (
            not re.fullmatch(r"[a-z][a-z0-9_]+", name)
            or handler not in HANDLERS
            or group not in {"app", "optional", "language"}
            or any(not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", item) for item in packages)
        ):
            raise ValueError(f"Invalid release catalog entry: {name}")
        blocker = None
        if handler == "catalog_deb" and arch not in row.get("artifacts", {}):
            blocker = f"No reviewed {name} Debian artifact for {arch} in this release"
        if kind is None or kind == group:
            result.append(Component(name, group, row["title"], handler, packages, blocker))
    return tuple(result)


def selected_components(settings: UserSettings) -> tuple[Component, ...]:
    selected = {*settings.apps, *settings.optional_apps, *settings.language_profiles}
    return tuple(entry for entry in components() if entry.id in selected)
