"""Read and operation boundaries used by the interactive application."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import Event
from typing import Protocol

from setup_system.application.interaction import Interaction
from setup_system.domain.catalog import ComponentCatalog
from setup_system.domain.displays import DisplayOutput, MonitorLayout
from setup_system.domain.operations import InstallRequest, RunStatus, WorkstationPlan
from setup_system.domain.settings import SystemSettings, ThemeSettings, UserSettings
from setup_system.domain.tool_requests import ToolChoices, ToolRequest


class InstallationPort(Protocol):
    interaction: Interaction
    cancel: Event

    @property
    def completed(self) -> list[str]: ...
    @property
    def status(self) -> RunStatus | None: ...
    def run(self, request: InstallRequest) -> int: ...
    def plan(self, request: InstallRequest) -> WorkstationPlan: ...


class ToolPort(Protocol):
    def choices(self) -> ToolChoices: ...
    def describe(self, request: ToolRequest) -> tuple[str, ...]: ...
    def execute(self, request: ToolRequest, *, confirmed: bool = False) -> str: ...


@dataclass(frozen=True)
class Intentions:
    user: UserSettings
    system: SystemSettings
    user_revision: str | None
    draft_revision: str | None
    system_revision: str | None
    warning: str | None


class SettingsPort(Protocol):
    def user(self) -> tuple[UserSettings, str | None]: ...
    def draft(self) -> tuple[SystemSettings, str | None]: ...
    def read(self) -> Intentions: ...
    def system(self) -> tuple[SystemSettings, str | None]: ...
    def check_draft(self, expected: str | None) -> None: ...
    def save(
        self,
        user: UserSettings,
        system: SystemSettings,
        user_revision: str | None,
        draft_revision: str | None,
    ) -> tuple[str, str]: ...


@dataclass(frozen=True)
class DisplayControls:
    discover: Callable[[], tuple[DisplayOutput, ...]]
    validate: Callable[[tuple[MonitorLayout, ...]], tuple[MonitorLayout, ...]]
    preview: Callable[[tuple[MonitorLayout, ...]], None]
    seconds: int


@dataclass(frozen=True)
class ViewServices:
    catalog: ComponentCatalog
    cleanup: tuple[str, ...]
    palette: Mapping[str, str]
    preview: Callable[[ThemeSettings], str]
    last_attempt: Callable[[str], str | None]
    settings: SettingsPort
    tools: ToolPort
    displays: DisplayControls
