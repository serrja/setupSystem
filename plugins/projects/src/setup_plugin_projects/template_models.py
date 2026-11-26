"""Immutable plans and results for reviewed project templates."""

import json
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    BeforeValidator,
    Field,
    JsonValue,
    PlainSerializer,
    StrictBool,
    TypeAdapter,
    model_validator,
)
from setup_system_sdk.files.contracts import ContractModel

from setup_plugin_projects.models import TemplateMetadata, TemplateVersion

_JSON_ANSWERS = TypeAdapter(dict[str, JsonValue])


def answer_values(encoded: bytes) -> dict[str, JsonValue]:
    return _JSON_ANSWERS.validate_json(encoded, strict=True)


def snapshot_answers(value: bytes | dict[str, JsonValue]) -> bytes:
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


class TemplateSource(ContractModel):
    """A canonical GitHub source declared by the shared project catalog."""

    project: Annotated[str, Field(pattern=r"^[a-z][A-Za-z0-9]*/[A-Za-z][A-Za-z0-9]*$")]
    url: str
    metadata: TemplateMetadata

    @model_validator(mode="after")
    def canonical_origin(self) -> Self:
        if self.url != f"https://github.com/{self.project}.git":
            raise ValueError("Template origin must match its canonical GitHub project")
        return self


class TemplatePlan(ContractModel):
    """Explicit inputs to one copy or update; no filesystem work during construction."""

    operation: Literal["create", "update"]
    source: TemplateSource
    version: TemplateVersion
    destination: Path
    cache_root: Path
    offline: StrictBool = False
    defaults: StrictBool = False
    data: AnswerSnapshot = b"{}"

    @model_validator(mode="after")
    def reviewed_version(self) -> Self:
        if self.version not in self.source.metadata.versions:
            raise ValueError("Template version is absent from the reviewed source")
        return self


class TemplateResult(ContractModel):
    """Identity and next actions after the official generator returns."""

    destination: Path
    template: str
    version: str
    source_commit: str
    project_name: str
    status: Literal["created", "updated", "conflicts"]
