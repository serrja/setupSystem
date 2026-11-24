"""Validated intentions, native task results and immutable consent snapshots."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictFloat,
    StrictInt,
    field_validator,
    model_validator,
)

from setup_system.domain.displays import MonitorLayout
from setup_system.domain.receipts import RunId

Section = Literal["base", "workspace"]
Monitor = MonitorLayout
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]


class Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
        hide_input_in_errors=True,
    )


class BaseOptions(Model):
    hyprland: StrictBool = False
    graphical_login: StrictBool = False
    plymouth: StrictBool = False
    timezone: str | None = Field(
        None, max_length=100, pattern=r"^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$"
    )
    locale: str | None = Field(None, max_length=64, pattern=r"^(C|[a-z]{2,3}_[A-Z]{2})\.UTF-8$")
    keyboard: str | None = Field(
        None, pattern=r"^[a-z][a-z0-9_-]{0,15}(,[a-z][a-z0-9_-]{0,15}){0,3}$"
    )

    @field_validator("timezone")
    @classmethod
    def canonical_zone(cls, value: str | None) -> str | None:
        if value and value.partition("/")[0] in {"posix", "right"}:
            raise ValueError("Choose a canonical timezone")
        return value


class Theme(Model):
    compact_panel: StrictBool = False
    effects: StrictBool = False
    opacity: StrictFloat = Field(default=0.96, ge=0.75, le=1)
    rounding: StrictInt = Field(default=12, ge=0, le=24)


class WorkspaceOptions(Model):
    desktop_theme: StrictBool = False
    theme: Theme = Theme()
    monitors: tuple[Monitor, ...] = ()
    apps: tuple[Identifier, ...] = ()
    languages: tuple[Identifier, ...] = ()
    cleanup: tuple[Identifier, ...] = ()
    cleanup_mode: Literal["remove", "purge", "autoremove"] = "remove"
    project: str | None = Field(None, max_length=4000, min_length=1, pattern=r"^[^\x00-\x1f\x7f]+$")

    @field_validator("project")
    @classmethod
    def absolute_project(cls, value: str | None) -> str | None:
        if value is not None and (not Path(value).is_absolute() or ".." in Path(value).parts):
            raise ValueError("Choose an absolute project path")
        return value

    @model_validator(mode="after")
    def unique(self) -> Self:
        for values in (
            self.apps,
            self.languages,
            self.cleanup,
            tuple(m.output for m in self.monitors),
        ):
            if len(values) != len(set(values)):
                raise ValueError("Duplicate selection")
        return self


class CleanupOptions(Model):
    cleanup: tuple[Identifier, ...]
    cleanup_mode: Literal["remove", "purge", "autoremove"]

    @field_validator("cleanup")
    @classmethod
    def distinct(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("Duplicate cleanup selection")
        return values


class RestoreOptions(Model):
    backup_id: RunId


ProjectTask = Literal[
    "project-prepare",
    "template-cache",
    "template-create",
    "template-update",
    "git-configure",
    "git-use",
    "shell-configure",
    "rtk-install",
]
PlanTask = Literal["base", "workspace", "cleanup", "restore"] | ProjectTask
Name = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[^\x00-\x1f\x7f]+$")]


def absolute_location(value: Path) -> Path:
    if (
        not value.is_absolute()
        or ".." in value.parts
        or any(ord(c) < 32 or ord(c) == 127 for c in str(value))
    ):
        raise ValueError("Choose an absolute path without parent traversal")
    return value


Location = Annotated[Path, AfterValidator(absolute_location)]


class Empty(Model):
    """An operation without user-supplied arguments."""


class ProjectChoice(Model):
    project_id: Name


class TemplateChoice(Model):
    template: Name
    version: Name | None = None
    offline: StrictBool = False


class TemplateDestination(TemplateChoice):
    destination: Location


class CreateTemplate(TemplateDestination):
    project_name: (
        Annotated[str, Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$", max_length=128)] | None
    ) = None
    python_profile: Literal["library", "service", "native"] | None = None

    @model_validator(mode="after")
    def profile_matches_template(self) -> Self:
        if self.python_profile is not None and self.template != "python":
            raise ValueError("Only the Python template accepts a Python profile")
        return self


class Repository(Model):
    repository: Location


class Profile(Repository):
    name: Name


class CreateProfile(Profile):
    owner_directory: Location | None = None


_PROJECT_MODELS: dict[str, type[Model]] = {
    "project-list": Empty,
    "project-prepare": Empty,
    "project-editor": ProjectChoice,
    "project-codex": ProjectChoice,
    "template-versions": TemplateChoice,
    "template-cache": TemplateChoice,
    "template-create": CreateTemplate,
    "template-update": TemplateDestination,
    "git-inspect": Repository,
    "git-configure": CreateProfile,
    "git-use": Profile,
    "shell-configure": Empty,
    "rtk-install": Empty,
}


def validate_project_settings(task: str, fields: object) -> dict[str, object]:
    """Validate native project input before any command, network or filesystem access."""
    model = _PROJECT_MODELS.get(task)
    if model is None:
        raise ValueError("Unknown project operation")
    return model.model_validate(fields).model_dump(mode="json", exclude_none=True)


Status = Literal[
    "ready",
    "blocked",
    "plan_ready",
    "no_changes",
    "nothing_selected",
    "complete",
    "needs_reboot",
    "partial",
    "failed",
    "cancelled",
    "requires_user_login",
]


class TaskResult(Model):
    status: Status
    actions: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    observed: dict[str, JsonValue] = Field(default_factory=dict)

    def describe(self) -> str:
        lines = [self.status.replace("_", " ").capitalize(), *self.actions, *self.blockers]
        return "\n".join(lines)


class PlanSnapshot(Model):
    """Keep the exact JSON sent and received, including native identity and closure."""

    task: PlanTask
    settings_json: str
    expected_json: str
    revision: str | None

    @classmethod
    def capture(
        cls,
        task: PlanTask,
        settings: dict[str, object],
        reply: dict[str, object],
        revision: str | None,
    ) -> Self:
        return cls(
            task=task,
            settings_json=json.dumps(settings, allow_nan=False),
            expected_json=json.dumps(reply, allow_nan=False),
            revision=revision,
        )

    @model_validator(mode="after")
    def executable(self) -> Self:
        models: dict[str, type[Model]] = {
            "base": BaseOptions,
            "workspace": WorkspaceOptions,
            "cleanup": CleanupOptions,
            "restore": RestoreOptions,
            **_PROJECT_MODELS,
        }
        models[self.task].model_validate_json(self.settings_json)
        result = TaskResult.model_validate_json(self.expected_json)
        if result.status not in {"plan_ready", "ready"} or result.blockers or not result.actions:
            raise ValueError("This plan has no executable changes")
        if self.task in {"base", "workspace"}:
            flags = tuple(result.observed.get(key) for key in ("needs_system", "needs_user"))
            if any(type(value) is not bool for value in flags) or not any(flags):
                raise ValueError("The plan did not identify its execution scope")
        return self

    @property
    def needs_password(self) -> bool:
        return self.task == "cleanup" or (
            self.task in {"base", "workspace"}
            and json.loads(self.expected_json)["observed"]["needs_system"]
        )

    def payload(self) -> dict[str, object]:
        return {
            "schema": 1,
            "settings": json.loads(self.settings_json),
            "expected": json.loads(self.expected_json),
        }
