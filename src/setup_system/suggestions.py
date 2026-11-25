"""Read-only completion sources for the Textual forms."""

from __future__ import annotations

import asyncio
import os
import tomllib
from itertools import islice
from pathlib import Path
from typing import Any
from zoneinfo import available_timezones

from textual.suggester import Suggester, SuggestFromList

from setup_system.configuration import ApplicationOptions
from setup_system.domain.applications import applications
from setup_system.domain.intentions import Section
from setup_system.domain.observations import NativeObservations
from setup_system.domain.projects import ProjectCatalog
from setup_system.layout import resource_root


class KeyboardChoices(SuggestFromList):
    async def get_suggestion(self, value: str) -> str | None:
        before, comma, typed = value.rpartition(",")
        choice = await super().get_suggestion(typed) if typed else None
        return before + comma + choice if choice and choice not in before.split(",") else None


class ProjectChoices(Suggester):
    def __init__(self, source: Suggestions):
        super().__init__(use_cache=False, case_sensitive=True)
        self.source = source
        self._pending: asyncio.Task[str | None] | None = None

    async def get_suggestion(self, value: str) -> str | None:
        if self._pending is not None:
            self._pending.cancel()
        search = asyncio.create_task(self._complete(value)) if value else None
        self._pending = search
        if search is None:
            return None
        try:
            return await search
        except asyncio.CancelledError:
            if self._pending is search:
                raise
            return None
        finally:
            if self._pending is search:
                self._pending = None

    async def _complete(self, value: str) -> str | None:
        await asyncio.sleep(self.source.options.suggestion_debounce_seconds)
        choices = await self.source.project_values(value)
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
    def __init__(self, config: Path, home: Path, options: ApplicationOptions) -> None:
        self.options = options
        self.config, self.home = config, home
        self.timezones: tuple[str, ...] = ()
        self.locales: tuple[str, ...] = ()
        self.keyboards: tuple[str, ...] = ()
        self.modes: dict[str, tuple[str, ...]] = {}
        self.projects: tuple[str, ...] = ()
        self.backups: tuple[str, ...] = ()
        self._scan_lock = asyncio.Lock()
        self.project_dirs: dict[Path, tuple[str, ...]] = {}

    def _catalog_paths(self) -> tuple[str, ...]:
        source = self.config / "workspace/projects.toml"
        catalog = ProjectCatalog.model_validate(_toml(source))
        root = self.home / catalog.projects_root.removeprefix("$HOME/")
        paths = (root / record.id for record in catalog.project)
        return tuple(
            str(path)
            for path in paths
            if path.is_relative_to(self.home) and ".." not in path.parts and path.is_dir()
        )

    async def refresh(self, section: Section, observed: NativeObservations) -> None:
        if section == "base":
            self.timezones = observed.timezones or tuple(
                sorted(await asyncio.to_thread(available_timezones))
            )
            self.locales = observed.locales
            self.keyboards = observed.keyboards
        else:
            async with self._scan_lock:
                self.project_dirs.clear()
                paths = await asyncio.to_thread(self._catalog_paths)
            self.projects = tuple(sorted(set(paths)))
            self.backups = observed.backups
        self.modes = {output.output: output.modes for output in observed.outputs}

    def _scan(self, directory: Path) -> tuple[str, ...]:
        try:
            with os.scandir(directory) as entries:
                return tuple(
                    sorted(
                        islice(
                            (
                                entry.path
                                for entry in islice(entries, self.options.suggestion_scan_entries)
                                if not entry.name.startswith(".") and entry.is_dir()
                            ),
                            self.options.suggestion_results,
                        )
                    )
                )
        except OSError:
            return ()

    async def project_values(self, value: str) -> tuple[str, ...]:
        if not value:
            return self.projects
        expanded = value.replace("~/", str(self.home) + "/", 1) if value.startswith("~/") else value
        path = Path(expanded)
        directory = path if expanded.endswith("/") else path.parent
        if not directory.is_absolute() or ".." in directory.parts:
            return self.projects
        async with self._scan_lock:
            if directory not in self.project_dirs:
                if len(self.project_dirs) >= self.options.suggestion_cache_directories:
                    self.project_dirs.pop(next(iter(self.project_dirs)))
                self.project_dirs[directory] = await asyncio.to_thread(self._scan, directory)
            candidates = self.project_dirs[directory]
        if value.startswith("~/"):
            candidates = tuple("~/" + os.path.relpath(item, self.home) for item in candidates)
        return tuple(dict.fromkeys((*self.projects, *candidates)))
