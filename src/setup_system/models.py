"""Validated intentions, native task results and immutable consent snapshots."""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self, get_args

from pydantic import (
    AfterValidator,
    Field,
    JsonValue,
    StrictBool,
    TypeAdapter,
    field_validator,
    model_validator,
)

from setup_system.domain.actions import Action
from setup_system.domain.administration import AdminAccount, AdminIntent
from setup_system.domain.contracts import MAX_MESSAGE_BYTES
from setup_system.domain.contracts import ContractModel as Model
from setup_system.domain.intentions import BaseOptions as BaseOptions
from setup_system.domain.intentions import CleanupMode, Identifier
from setup_system.domain.intentions import Monitor as Monitor
from setup_system.domain.intentions import Section as Section
from setup_system.domain.intentions import Theme as Theme
from setup_system.domain.intentions import WorkspaceOptions as WorkspaceOptions
from setup_system.domain.manifest_types import Digest
from setup_system.domain.observations import NativeObservations, Observations, ProjectObservations
from setup_system.domain.receipts import RunId


class CleanupOptions(Model):
    cleanup: tuple[Identifier, ...]
    cleanup_mode: CleanupMode

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
PlanTask = Literal["base", "workspace", "cleanup", "restore", "admin"] | ProjectTask
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


class ProjectDirectory(Model):
    project: Location


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


class Status(StrEnum):
    """Stable outcome identifiers in the native operation protocol."""

    READY = "ready"
    BLOCKED = "blocked"
    PLAN_READY = "plan_ready"
    NO_CHANGES = "no_changes"
    NOTHING_SELECTED = "nothing_selected"
    COMPLETE = "complete"
    NEEDS_REBOOT = "needs_reboot"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REQUIRES_USER_LOGIN = "requires_user_login"

    @property
    def allows_confirmation(self) -> bool:
        return self in (Status.PLAN_READY, Status.READY)


class AdminObservations(Observations):
    account: AdminAccount
    boot_identity: Digest


def bounded_json(value: object) -> object:
    """Bound nesting and object cardinality for direct callers as well as process input."""
    work = [(value, 0)]
    count = 0
    characters = 0
    while work:
        node, depth = work.pop()
        count += 1
        if count > 48000 or depth > 24 or characters > MAX_MESSAGE_BYTES:
            raise ValueError("Operation payload exceeds the supported structure limits")
        if isinstance(node, dict):
            if len(node) > 8192 or any(not isinstance(k, str) or len(k) > 4096 for k in node):
                raise ValueError("Invalid operation data keys")
            characters += sum(len(k) for k in node)
            work.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, (tuple, list)):
            if len(node) > 8192:
                raise ValueError("Operation collection exceeds the limit")
            work.extend((v, depth + 1) for v in node)
        elif isinstance(node, str):
            characters += len(node)
            if len(node) > 16384 or characters > MAX_MESSAGE_BYTES:
                raise ValueError("Operation text exceeds the limit")
    return value


class TaskResult(Model):
    status: Status
    actions: tuple[Action, ...] = Field(default=(), max_length=8192)
    blockers: tuple[str, ...] = Field(default=(), max_length=512)
    observed: NativeObservations | ProjectObservations | AdminObservations = Field(
        default_factory=NativeObservations
    )

    @model_validator(mode="before")
    @classmethod
    def bounded(cls, value: object) -> object:
        return bounded_json(value)

    def describe(self) -> str:
        lines = [
            self.status.replace("_", " ").capitalize(),
            *(action.label for action in self.actions),
            *self.blockers,
        ]
        if isinstance(self.observed, AdminObservations):
            account = self.observed.account
            membership = "yes" if account.member else "no"
            lines.extend(
                [
                    f"Account {account.username} (UID {account.uid}, GID {account.gid})",
                    f"Home: {account.home}",
                    f"Login shell: {account.shell}",
                    f"Member of sudo group (GID {account.sudo_gid}): {membership}",
                ]
            )
            if self.status is Status.NO_CHANGES and account.member:
                lines.append("Effective sudo access requires a privileged policy check.")
        return "\n".join(lines)

    def same_plan(self, other: TaskResult) -> bool:
        """Compare ordered operation inputs and facts independently of action labels."""
        exclude = {"actions": {"__all__": {"label"}}}
        return self.model_dump(
            mode="json", exclude=exclude, exclude_unset=True
        ) == other.model_dump(mode="json", exclude=exclude, exclude_unset=True)


def settings_model(task: str) -> type[Model]:
    models: dict[str, type[Model]] = {
        "base": BaseOptions,
        "workspace": WorkspaceOptions,
        "cleanup": CleanupOptions,
        "display-preview": WorkspaceOptions,
        "project-check": ProjectDirectory,
        "restore": RestoreOptions,
        "admin": AdminIntent,
        **_PROJECT_MODELS,
    }
    if task not in models:
        raise ValueError("Unknown operation target")
    return models[task]


class RequestEnvelope(Model):
    schema_version: Literal[1] = Field(alias="schema")
    settings: dict[str, JsonValue]
    expected: TaskResult | None

    @model_validator(mode="before")
    @classmethod
    def bounded(cls, value: object) -> object:
        return bounded_json(value)

    def for_target(self, target: str) -> Self:
        operations = {
            "base": {"inspect", "plan", "apply", "user-apply"},
            "workspace": {"inspect", "plan", "apply", "user-apply"},
            "admin": {"inspect", "plan", "apply"},
            "restore": {"plan", "apply"},
            "cleanup": {"plan", "apply"},
            **{key: {"plan", "apply"} for key in get_args(ProjectTask)},
        }
        if target in {
            "project-check",
            "display-preview",
            *(_PROJECT_MODELS.keys() - operations.keys()),
        }:
            operation = target
        else:
            names = {
                f"{key}-{phase}": key for key, phases in operations.items() for phase in phases
            }
            if target not in names:
                raise ValueError("Unknown operation target")
            operation = names[target]
        settings_model(operation).model_validate(self.settings)
        if target.endswith("-apply"):
            if self.expected is None:
                raise ValueError("Apply requires a confirmed plan")
            plan = PlanSnapshot.capture(
                TypeAdapter(PlanTask).validate_python(operation),
                self.settings,
                self.expected.model_dump(mode="json", exclude_unset=True),
                None,
            )
            if (
                operation in {"base", "workspace"}
                and target.endswith("-user-apply") == plan.needs_password
            ):
                raise ValueError("The Apply target must match the plan's system and user scope")
        elif self.expected is not None:
            raise ValueError("Read-only operations cannot consume a confirmed plan")
        return self


class PlanSnapshot(Model):
    """Keep the exact JSON sent and received, including native identity and closure."""

    task: PlanTask
    settings_json: str = Field(max_length=MAX_MESSAGE_BYTES)
    expected_json: str = Field(max_length=MAX_MESSAGE_BYTES)
    revision: str | None = Field(max_length=256)

    @classmethod
    def capture(
        cls,
        task: PlanTask,
        settings: Mapping[str, object],
        reply: Mapping[str, object],
        revision: str | None,
    ) -> Self:
        bounded_json(dict(settings))
        bounded_json(dict(reply))
        return cls(
            task=task,
            settings_json=json.dumps(dict(settings), allow_nan=False),
            expected_json=json.dumps(dict(reply), allow_nan=False),
            revision=revision,
        )

    @model_validator(mode="after")
    def executable(self) -> Self:
        settings = settings_model(self.task).model_validate_json(self.settings_json)
        result = TaskResult.model_validate_json(self.expected_json)
        if not result.status.allows_confirmation or result.blockers or not result.actions:
            raise ValueError("This plan has no executable changes")
        if isinstance(settings, AdminIntent):
            if (
                not isinstance(result.observed, AdminObservations)
                or result.observed.account.username != settings.username
                or result.observed.account.member
                or not result.observed.needs_system
                or result.observed.needs_user
                or len(result.actions) != 1
                or any(a.kind != "admin" or a.username != settings.username for a in result.actions)
            ):
                raise ValueError("Administrative consent must identify the same ordinary account")
        if self.task in {"base", "workspace"}:
            kinds = {action.kind for action in result.actions}
            system = (
                {"apt", "region", "graphical_login", "plymouth"}
                if self.task == "base"
                else {"apt", "happ_start", "firefox_policy"}
            )
            user = (
                {"user_file"}
                if self.task == "base"
                else {"user_file", "tool", "parsers", "editor_verify"}
            )
            flags = (result.observed.get("needs_system"), result.observed.get("needs_user"))
            if kinds - (system | user) or flags != (bool(kinds & system), bool(kinds & user)):
                raise ValueError("The native actions and their confirmed execution scope differ")
        elif self.task == "cleanup":
            if any(
                a.kind != "apt" or not a.record.startswith(("Remv ", "Purg "))
                for a in result.actions
            ):
                raise ValueError("Cleanup must confirm the native removal records")
        elif self.task == "restore":
            if any(a.kind != "restore" for a in result.actions):
                raise ValueError("Restore must confirm journal-based file changes")
        elif self.task != "admin" and any(a.kind != self.task for a in result.actions):
            raise ValueError("The selected project action differs from the confirmed task")
        return self

    @property
    def needs_password(self) -> bool:
        return self.task in {"cleanup", "admin"} or (
            self.task in {"base", "workspace"}
            and json.loads(self.expected_json)["observed"]["needs_system"]
        )

    def payload(self) -> dict[str, object]:
        return {
            "schema": 1,
            "settings": json.loads(self.settings_json),
            "expected": json.loads(self.expected_json),
        }
