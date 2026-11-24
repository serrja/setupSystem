"""Confirm an immutable plan before requesting a password for a system change."""

from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from setup_system.models import PlanSnapshot, TaskResult
from setup_system.runner import BecomePassword


class Consent(ModalScreen[bool]):
    BINDINGS: ClassVar[list[BindingType]] = [("escape", "cancel", "Cancel")]

    def __init__(self, plan: PlanSnapshot):
        super().__init__()
        self.plan = plan

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Label("Apply the reviewed plan?")
            with VerticalScroll(id="review"):
                yield Static(
                    TaskResult.model_validate_json(self.plan.expected_json).describe(), markup=False
                )
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="back")
                yield Button("Confirm apply", id="confirm", variant="warning")

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)


class Password(ModalScreen[BecomePassword | None]):
    BINDINGS: ClassVar[list[BindingType]] = [("escape", "cancel", "Cancel")]

    def __init__(self, *, root_account: bool = False) -> None:
        super().__init__()
        self.root_account = root_account

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Label(
                "Root password for su to grant sudo access"
                if self.root_account
                else "Your sudo password for the confirmed system operation"
            )
            yield Input(password=True, id="password")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="back")
                yield Button("Authorize", id="authorize", variant="warning")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def submitted(self) -> None:
        self.authorize()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "authorize":
            self.authorize()
        else:
            self.action_cancel()

    def authorize(self) -> None:
        field = self.query_one(Input)
        value = field.value
        field.value = ""
        if value:
            try:
                response = BecomePassword(value)
            except ValueError:
                field.placeholder = "Enter a valid single-line password"
                field.focus()
                return
            self.dismiss(response)
        else:
            field.focus()

    def action_cancel(self) -> None:
        self.query_one(Input).value = ""
        self.dismiss(None)
