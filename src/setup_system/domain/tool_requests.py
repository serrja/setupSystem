"""Closed, immutable workspace actions selected by subject-specific forms."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, JsonValue, PlainSerializer, TypeAdapter

from setup_system.domain.contracts import ContractModel
from setup_system.domain.receipts import RunId

Name = Annotated[str, Field(min_length=1, max_length=256)]
_JSON_ANSWERS = TypeAdapter(dict[str, JsonValue])


def answer_values(encoded: bytes) -> dict[str, JsonValue]:
    return _JSON_ANSWERS.validate_json(encoded, strict=True)


def snapshot_answers(value: object) -> bytes:
    values = (
        answer_values(value)
        if isinstance(value, bytes)
        else _JSON_ANSWERS.validate_python(value, strict=True)
    )
    if any(key.startswith("_") for key in values):
        raise ValueError("Internal Copier answers cannot be supplied")
    return json.dumps(
        values, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


AnswerSnapshot = Annotated[bytes, BeforeValidator(snapshot_answers), PlainSerializer(answer_values)]
SimpleAction = Literal[
    "projects",
    "prepare_projects",
    "templates",
    "shell",
    "rtk",
    "git_profiles",
    "font_plan",
    "font_apply",
    "font_probe",
]


class SimpleTool(ContractModel):
    action: SimpleAction


class OpenProject(ContractModel):
    action: Literal["open_project", "open_codex"]
    name: Name


class WriteTemplate(ContractModel):
    action: Literal["create_template", "update_template"]
    name: Name
    destination: Path
    version: Name | None = None
    answers: AnswerSnapshot = b"{}"


class CacheTemplate(ContractModel):
    action: Literal["template_cache"] = "template_cache"
    name: Name
    version: Name | None = None


class RestoreTool(ContractModel):
    action: Literal["restore", "restore_preview"]
    backup_id: RunId


class InspectGit(ContractModel):
    action: Literal["git_inspect"] = "git_inspect"
    destination: Path


class UseGitProfile(ContractModel):
    action: Literal["git_use"] = "git_use"
    name: Name
    destination: Path


class ConfigureGit(ContractModel):
    action: Literal["git_configure"] = "git_configure"
    name: Name
    destination: Path
    owner_directory: Path | None = None


ToolRequest = Annotated[
    SimpleTool
    | OpenProject
    | WriteTemplate
    | CacheTemplate
    | RestoreTool
    | InspectGit
    | UseGitProfile
    | ConfigureGit,
    Field(discriminator="action"),
]
TOOL_REQUEST = TypeAdapter(ToolRequest)


def mutates(request: ToolRequest) -> bool:
    return request.action not in {
        "projects",
        "templates",
        "restore_preview",
        "git_inspect",
        "git_profiles",
        "font_plan",
        "font_probe",
    }


@dataclass(frozen=True)
class TemplateChoice:
    name: str
    versions: tuple[str, ...]
    recommended: str | None


@dataclass(frozen=True)
class ToolChoices:
    projects: tuple[str, ...]
    templates: tuple[TemplateChoice, ...]
    history: tuple[tuple[str, str], ...]
