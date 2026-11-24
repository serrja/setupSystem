"""Read-only completion sources for the Textual forms."""

from __future__ import annotations

import asyncio
import os
import tomllib
from itertools import islice
from pathlib import Path
from typing import Any
from zoneinfo import available_timezones

from textual.suggester import Suggester

from setup_system.domain.applications import applications
from setup_system.domain.intentions import Section
from setup_system.domain.observations import NativeObservations
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
        self.generation = 0

    async def get_suggestion(self, value: str) -> str | None:
        self.generation += 1
        generation = self.generation
        if not value:
            return None
        await asyncio.sleep(0.075)
        if self.generation != generation:
            return None
        choices = await self.source.project_values(value)
        if self.generation != generation:
            return None
        return next((path for path in choices if path.startswith(value)), None)


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
        self._scan_lock = asyncio.Lock()
        self.project_dirs: dict[Path, tuple[str, ...]] = {}

    def _catalog_paths(self) -> tuple[str, ...]:
        source = self.config / "workspace/projects.toml"
        catalog = _toml(source)
        root = Path(str(catalog["projectsRoot"]).replace("$HOME/", str(self.home) + "/", 1))
        paths = (root / entry["id"] for entry in catalog.get("project", ()))
        return tuple(
            str(path)
            for path in paths
            if path.is_relative_to(self.home) and ".." not in path.parts and path.is_dir()
        )

    async def refresh(self, section: Section, observed: NativeObservations) -> None:
        if section == "base":
            self.timezones.values = observed.timezones or tuple(
                sorted(await asyncio.to_thread(available_timezones))
            )
            self.locales.values = observed.locales
            self.keyboards.values = observed.keyboards
        else:
            async with self._scan_lock:
                self.project_dirs.clear()
                paths = await asyncio.to_thread(self._catalog_paths)
            self.projects.values = tuple(sorted(set(paths)))
            self.backups.values = observed.backups
        self.modes = {output.output: output.modes for output in observed.outputs}
        self.outputs.values = tuple(self.modes)

    @staticmethod
    def _scan(directory: Path) -> tuple[str, ...]:
        try:
            with os.scandir(directory) as entries:
                return tuple(
                    sorted(
                        islice(
                            (
                                entry.path
                                for entry in islice(entries, 2048)
                                if not entry.name.startswith(".") and entry.is_dir()
                            ),
                            128,
                        )
                    )
                )
        except OSError:
            return ()

    async def project_values(self, value: str) -> tuple[str, ...]:
        if not value:
            return self.projects.values
        expanded = value.replace("~/", str(self.home) + "/", 1) if value.startswith("~/") else value
        path = Path(expanded)
        directory = path if expanded.endswith("/") else path.parent
        if not directory.is_absolute() or ".." in directory.parts:
            return self.projects.values
        async with self._scan_lock:
            if directory not in self.project_dirs:
                if len(self.project_dirs) >= 32:
                    self.project_dirs.pop(next(iter(self.project_dirs)))
                self.project_dirs[directory] = await asyncio.to_thread(self._scan, directory)
            candidates = self.project_dirs[directory]
        if value.startswith("~/"):
            candidates = tuple("~/" + os.path.relpath(item, self.home) for item in candidates)
        return tuple(dict.fromkeys((*self.projects.values, *candidates)))
