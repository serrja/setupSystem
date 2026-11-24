"""Read-only completion sources for the Textual forms."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any
from zoneinfo import available_timezones

from textual.suggester import Suggester

from setup_system.domain.applications import applications
from setup_system.runner import resource_root


class Choices(Suggester):
    """Textual awaits this method on each edit; sources stay in memory until refresh."""

    def __init__(self, values: tuple[str, ...] = ()) -> None:
        super().__init__(use_cache=False, case_sensitive=True)
        self.values = values

    async def get_suggestion(self, value: str) -> str | None:
        return next((item for item in self.values if item.startswith(value) and value), None)


class KeyboardChoices(Choices):
    async def get_suggestion(self, value: str) -> str | None:
        before, comma, typed = value.rpartition(",")
        choice = await super().get_suggestion(typed)
        return before + comma + choice if choice and choice not in before.split(",") else None


class ProjectChoices(Suggester):
    def __init__(self, source: Suggestions):
        super().__init__(use_cache=False, case_sensitive=True)
        self.source = source

    async def get_suggestion(self, value: str) -> str | None:
        return next(
            (
                path
                for path in self.source.project_values(value)
                if value and path.startswith(value)
            ),
            None,
        )


def config_root() -> Path:
    return resource_root() / "config"


def _toml(path: Path) -> dict[str, Any]:
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("Catalog paths cannot traverse symlinks")
    with path.open("rb") as stream:
        return tomllib.load(stream)


class Catalog:
    def __init__(self, config: Path) -> None:
        self.config = config
        entries = applications(_toml(config / "workspace/applications.toml"))
        self.apps = {key: entry.title for key, entry in entries.items() if entry.kind != "language"}
        self.languages = {
            key: entry.title for key, entry in entries.items() if entry.kind == "language"
        }
        self.cleanup = tuple(_toml(config / "workspace/optional_cleanup.toml")["candidates"])


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(sorted({item for item in value if isinstance(item, str) and item}))


class Suggestions:
    def __init__(self, config: Path, home: Path) -> None:
        self.config, self.home = config, home
        self.timezones = Choices()
        self.locales = Choices()
        self.keyboards = KeyboardChoices()
        self.outputs = Choices()
        self.modes: dict[str, tuple[str, ...]] = {}
        self.projects = Choices()
        self.backups = Choices()
        self.project_dirs: dict[Path, tuple[str, ...]] = {}

    def refresh(self, section: str, observed: dict[str, Any]) -> None:
        if section == "base":
            self.timezones.values = _strings(observed.get("timezones")) or tuple(
                sorted(available_timezones())
            )
            self.locales.values = _strings(observed.get("locales"))
            self.keyboards.values = _strings(observed.get("keyboards"))
        if section == "workspace":
            self.project_dirs.clear()
            source = self.config / "workspace/projects.toml"
            paths: list[str] = []
            if source.is_file():
                catalog = _toml(source)
                root = Path(
                    str(catalog.get("projectsRoot", "$HOME/Projects")).replace(
                        "$HOME/", str(self.home) + "/", 1
                    )
                )
                for row in catalog.get("project", []):
                    if isinstance(row, dict) and isinstance(row.get("id"), str):
                        path = root / row["id"]
                        if (
                            path.is_absolute()
                            and path.is_relative_to(self.home)
                            and ".." not in path.parts
                            and path.is_dir()
                        ):
                            paths.append(str(path))
            self.projects.values = _strings(paths + list(_strings(observed.get("projects"))))
            self.backups.values = _strings(observed.get("backups"))
        if isinstance(observed.get("outputs"), list):
            self.modes.clear()
            for item in observed["outputs"]:
                if isinstance(item, dict) and isinstance(item.get("output"), str):
                    self.modes[item["output"]] = _strings(item.get("modes"))
            self.outputs.values = tuple(self.modes)

    def project_values(self, value: str) -> tuple[str, ...]:
        if not value:
            return self.projects.values
        expanded = value.replace("~/", str(self.home) + "/", 1) if value.startswith("~/") else value
        path = Path(expanded)
        directory = path if expanded.endswith("/") else path.parent
        if not directory.is_absolute() or ".." in directory.parts:
            return self.projects.values
        if directory not in self.project_dirs:
            try:
                self.project_dirs[directory] = tuple(
                    sorted(
                        str(entry)
                        for entry in directory.iterdir()
                        if entry.is_dir() and not entry.name.startswith(".")
                    )
                )
            except OSError:
                self.project_dirs[directory] = ()
        candidates = self.project_dirs[directory]
        if value.startswith("~/"):
            candidates = tuple("~/" + os.path.relpath(item, self.home) for item in candidates)
        return tuple(dict.fromkeys((*self.projects.values, *candidates)))
