"""Workspace tools remain inside Textual and share its operation ownership."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, cast, get_args

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select

from setup_system.application.tools import ToolAction, ToolRequest


class ToolsScreen(ModalScreen[ToolRequest | None]):
    DEFAULT_CSS = """
    ToolsScreen { align: center middle; }
    ToolsScreen > VerticalScroll {
        width: 90%; height: 90%; padding: 1 2; border: round $accent;
    }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Close")]

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Label("Workspace tools · changes require confirmation")
            yield Select(
                [(name.replace("_", " "), name) for name in get_args(ToolAction)],
                value="projects",
                allow_blank=False,
                id="tool-action",
            )
            yield Input(placeholder="Exact project name / template alias", id="tool-name")
            yield Input(placeholder="Destination / exact repository root", id="tool-destination")
            yield Input(
                placeholder="Reviewed version (empty = catalog recommendation)", id="tool-version"
            )
            yield Input(value="{}", placeholder="Copier answers JSON", id="tool-data")
            yield Input(placeholder="Backup ID", id="tool-backup")
            yield Button("Continue", id="tool-run", variant="primary")
            yield Button("Close", id="tool-close")

    @on(Button.Pressed, "#tool-run")
    def submit(self) -> None:
        destination = self.query_one("#tool-destination", Input).value.strip()
        self.dismiss(
            ToolRequest(
                action=cast(
                    ToolAction,
                    self.query_one("#tool-action", Select).value,
                ),
                name=self.query_one("#tool-name", Input).value.strip(),
                destination=Path(destination).expanduser().absolute() if destination else None,
                version=self.query_one("#tool-version", Input).value.strip() or None,
                data=self.query_one("#tool-data", Input).value,
                backup_id=self.query_one("#tool-backup", Input).value.strip(),
            )
        )

    @on(Button.Pressed, "#tool-close")
    def action_cancel(self) -> None:
        self.dismiss(None)
