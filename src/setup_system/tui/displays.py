"""Edit only modes reported by the current user's compositor."""

from __future__ import annotations

from typing import ClassVar

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select

from setup_system.domain.displays import DisplayOutput, MonitorLayout
from setup_system.infrastructure.displays import PREVIEW_SECONDS, preview_layout, validate_layouts


class DisplaysScreen(ModalScreen[tuple[MonitorLayout, ...] | None]):
    DEFAULT_CSS = """
    DisplaysScreen {align:center middle;}
    DisplaysScreen > VerticalScroll { width:85%; height:90%; border:round $accent; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "close", "Close")]

    def __init__(
        self, outputs: tuple[DisplayOutput, ...], saved: tuple[MonitorLayout, ...]
    ) -> None:
        super().__init__()
        self.outputs = outputs
        self.saved = {entry.output: entry for entry in saved}
        self.previewed: tuple[MonitorLayout, ...] | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            for index, output in enumerate(self.outputs):
                name = output.current.output
                saved = self.saved.get(name, output.current)
                yield Checkbox(name, value=name in self.saved, id=f"use-{index}")
                yield Select(
                    [(mode, mode) for mode in output.modes],
                    allow_blank=False,
                    value=saved.mode if saved.mode in output.modes else output.current.mode,
                    id=f"mode-{index}",
                )
                for label in ("scale", "x", "y"):
                    yield Label(label.capitalize())
                    yield Input(str(getattr(saved, label)), id=f"{label}-{index}")
            yield Button(f"Preview for {PREVIEW_SECONDS} seconds", id="display-preview")
            yield Label("The preview restores the saved layout automatically.", id="display-status")
            yield Button("Use layout in next plan", id="display-use")
            yield Button("Close", id="display-close")

    def layouts(self) -> tuple[MonitorLayout, ...]:
        return tuple(
            MonitorLayout(
                output=output.current.output,
                mode=str(self.query_one(f"#mode-{i}", Select).value),
                scale=float(self.query_one(f"#scale-{i}", Input).value),
                x=int(self.query_one(f"#x-{i}", Input).value),
                y=int(self.query_one(f"#y-{i}", Input).value),
            )
            for i, output in enumerate(self.outputs)
            if self.query_one(f"#use-{i}", Checkbox).value
        )

    @on(Button.Pressed, "#display-preview")
    def preview(self) -> None:
        try:
            layouts = self.layouts()
            if not layouts:
                raise ValueError("Select at least one connected display")
        except ValueError as exc:
            self.notify(str(exc), severity="warning")
            return
        self.query_one("#display-preview", Button).disabled = True
        self.query_one("#display-use", Button).disabled = True
        self.run_preview(layouts)

    @work(thread=True, exit_on_error=False)
    def run_preview(self, layouts: tuple[MonitorLayout, ...]) -> None:
        try:
            preview_layout(layouts)
        except (OSError, ValueError, RuntimeError) as exc:
            self.app.call_from_thread(self.preview_result, None, str(exc))
        else:
            self.app.call_from_thread(
                self.preview_result, layouts, "Preview finished. Saved layout restored."
            )

    def preview_result(self, layouts: tuple[MonitorLayout, ...] | None, message: str) -> None:
        if self.is_mounted:
            self.previewed = layouts
            self.query_one("#display-preview", Button).disabled = False
            self.query_one("#display-use", Button).disabled = False
            self.query_one("#display-status", Label).update(message)

    @on(Button.Pressed, "#display-use")
    def accept(self) -> None:
        try:
            layouts = self.layouts()
            validate_layouts(layouts)
            if layouts and layouts != self.previewed:
                raise ValueError("Preview these exact display settings first")
        except (OSError, ValueError, RuntimeError) as exc:
            self.notify(str(exc), severity="warning")
            return
        self.dismiss(layouts)

    @on(Button.Pressed, "#display-close")
    def action_close(self) -> None:
        self.dismiss(None)
