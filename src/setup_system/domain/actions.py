"""Semantic operation records reached by the reviewed Make targets."""

from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from setup_system.domain.administration import AdminIntent
from setup_system.domain.contracts import ContractModel
from setup_system.domain.receipts import RunId
from setup_system.domain.system_receipts import RegionalKind

Line = Annotated[str, Field(min_length=1, max_length=4096, pattern=r"^[^\x00-\x1f\x7f]+$")]
Token = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9@_+./:~-]+$")]


def relative_name(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or str(path) != value:
        raise ValueError("Expected a canonical relative file name")
    return value


def absolute_name(value: str) -> str:
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Expected an absolute path without traversal")
    return value


RelativeName = Annotated[Line, AfterValidator(relative_name)]
AbsoluteName = Annotated[Line, AfterValidator(absolute_name)]


class LabeledAction(ContractModel):
    label: Line


class SystemAction(LabeledAction):
    kind: Literal[
        "happ_start",
        "graphical_login",
        "firefox_policy",
        "plymouth",
        "packages_verified",
        "editor_verify",
        "display_restored",
        "login_required",
    ]


class RegionAction(LabeledAction):
    kind: Literal["region"]
    field: RegionalKind
    value: Annotated[Line, Field(max_length=100, pattern=r"^[A-Za-z0-9_,+.\-/]+$")]


class AptAction(LabeledAction):
    kind: Literal["apt"]
    record: Annotated[Line, Field(pattern=r"^(Inst|Conf|Remv|Purg) [a-z0-9][a-z0-9+.:~-]*( |$)")]


class FileAction(LabeledAction):
    kind: Literal["user_file"]
    path: RelativeName


class ParserAction(LabeledAction):
    kind: Literal["parsers"]
    parsers: Annotated[tuple[Token, ...], Field(min_length=1, max_length=128)]


class ToolAction(LabeledAction):
    kind: Literal["tool"]
    name: Token
    version: Token


class ReceiptAction(LabeledAction):
    kind: Literal["backup"]
    backup_id: RunId


RestoreOperation = Literal[
    "would-create",
    "would-restore",
    "would-remove",
    "would-remove-directory",
    "would-return-displaced",
]


class RestoreAction(LabeledAction):
    kind: Literal["restore"]
    path: AbsoluteName
    operation: RestoreOperation


class ProjectAction(LabeledAction):
    kind: Literal[
        "project-prepare",
        "template-cache",
        "template-create",
        "template-update",
        "git-configure",
        "git-use",
        "shell-configure",
        "rtk-install",
    ]
    target: AbsoluteName
    fields: tuple[Line, ...] = ()


class AdminAction(AdminIntent, LabeledAction):
    kind: Literal["admin"]


Action = Annotated[
    SystemAction
    | RegionAction
    | AptAction
    | FileAction
    | ParserAction
    | ToolAction
    | ReceiptAction
    | RestoreAction
    | ProjectAction
    | AdminAction,
    Field(discriminator="kind"),
]
