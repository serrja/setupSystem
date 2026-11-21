"""Immutable executable and blocked stages of a reviewed workstation plan."""

from enum import StrEnum
from typing import Annotated, ClassVar, Literal, Self

from pydantic import Field, StrictBool, model_validator

from setup_system.domain.catalog import LanguageProfile, PackageName
from setup_system.domain.contracts import ContractModel
from setup_system.domain.login import LoginConsent
from setup_system.domain.manifest_types import Digest
from setup_system.domain.paths import XdgPaths
from setup_system.domain.settings import Section, SystemSettings, UserSettings
from setup_system.domain.system_commands import RegionalKind


class Operation(StrEnum):
    PLAN = "plan"
    APPLY = "apply"


class InstallRequest(ContractModel):
    operation: Operation
    user: str | None = None
    yes: StrictBool = False
    section: Section = Section.BASE
    system: SystemSettings = Field(default_factory=SystemSettings)
    settings: UserSettings = Field(default_factory=UserSettings)
    revision_known: StrictBool = False
    draft_revision: Digest | None = None
    user_revision: Digest | None = None
    system_revision: Digest | None = None


class RunStatus(StrEnum):
    READY = "ready"
    FAILED = "failed"
    BLOCKED = "blocked"
    PLAN_READY = "plan_ready"
    BUSY = "busy"
    NO_CHANGES = "no_changes"
    NOTHING_SELECTED = "nothing_selected"
    CANCELLED = "cancelled"
    PARTIAL = "partial"
    NEEDS_REBOOT = "needs_reboot"
    COMPLETE = "complete"


class Stage(ContractModel):
    description: str
    blockers: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    privileged: ClassVar[bool] = False


class SystemAction(Stage):
    kind: Literal["system"] = "system"
    id: Literal["base", "system_settings"]
    privileged: ClassVar[bool] = True


class LoginAction(Stage):
    kind: Literal["login"] = "login"
    id: Literal["login"] = "login"
    confirmed: LoginConsent
    privileged: ClassVar[bool] = True


class RegionAction(Stage):
    kind: Literal["region"] = "region"
    id: RegionalKind
    value: str
    before: str | None
    packages: tuple[PackageName, ...]
    privileged: ClassVar[bool] = True


class BootAction(Stage):
    kind: Literal["boot"] = "boot"
    id: Literal["plymouth"] = "plymouth"
    identity: Digest
    packages: tuple[PackageName, ...]
    privileged: ClassVar[bool] = True


class PackageAction(Stage):
    kind: Literal["packages"] = "packages"
    id: Literal["workspace_packages"] = "workspace_packages"
    packages: tuple[PackageName, ...]
    external: tuple[str, ...]
    before: str | None
    privileged: ClassVar[bool] = True


class CleanupAction(Stage):
    kind: Literal["cleanup"] = "cleanup"
    id: Literal["cleanup"] = "cleanup"
    packages: tuple[PackageName, ...]
    privileged: ClassVar[bool] = True


class DesktopAction(Stage):
    kind: Literal["desktop"] = "desktop"
    id: Literal["desktop_files", "theme"]


class ApplicationAction(Stage):
    kind: Literal["application"] = "application"
    id: Literal["neovim", "firefox", "codex"]


class LanguageAction(Stage):
    kind: Literal["language"] = "language"
    id: LanguageProfile


class BlockedAction(Stage):
    kind: Literal["unavailable"] = "unavailable"
    id: str
    blockers: Annotated[tuple[str, ...], Field(min_length=1)]


type Action = Annotated[
    SystemAction
    | LoginAction
    | RegionAction
    | BootAction
    | PackageAction
    | CleanupAction
    | DesktopAction
    | ApplicationAction
    | LanguageAction
    | BlockedAction,
    Field(discriminator="kind"),
]


class WorkstationPlan(ContractModel):
    section: Section
    system: SystemSettings
    user: UserSettings
    actions: tuple[Action, ...]
    observations: tuple[str, ...] = ()
    xdg: XdgPaths | None = None
    system_revision: Digest | None = None
    user_revision: Digest | None = None
    draft_revision: Digest | None = None
    matched: StrictBool = False

    @model_validator(mode="after")
    def ordered_dependencies(self) -> Self:
        previous: set[str] = set()
        for stage in self.actions:
            if stage.id in previous or set(stage.requires) - previous:
                raise ValueError("Duplicate stage or missing dependency in workstation plan")
            previous.add(stage.id)
        return self

    @property
    def blockers(self) -> tuple[str, ...]:
        return tuple(reason for action in self.actions for reason in action.blockers)

    def describe(self) -> tuple[str, ...]:
        return (
            *self.observations,
            *(f"{action.id}: {action.description}" for action in self.actions),
            *("BLOCKED: " + reason for reason in self.blockers),
            "Cancellation stops before the next stage; completed package changes remain.",
        )
