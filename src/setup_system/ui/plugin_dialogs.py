"""Keyboard-accessible consent, reference and trust prompts owned by the host."""

from typing import ClassVar

from pydantic import SecretStr
from textual import on
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static


class Confirm(ModalScreen[bool]):
    BINDINGS: ClassVar[list[BindingType]] = [("escape", "cancel", "Cancel")]

    def __init__(self, text: str, button: str) -> None:
        super().__init__()
        self.text = text
        self.caption = button

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            with VerticalScroll(classes="review-text"):
                yield Static(self.text, markup=False)
            yield Button("Cancel", id="back")
            yield Button(self.caption, id="approve", variant="warning")

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "approve")

    def action_cancel(self) -> None:
        self.dismiss(False)


class Prompt(ModalScreen[str | None]):
    BINDINGS: ClassVar[list[BindingType]] = [("escape", "cancel", "Cancel")]

    def __init__(self, label: str) -> None:
        super().__init__()
        self.label = label

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.label, markup=False)
            yield Input(id="answer", max_length=4096)
            yield Button("Continue", id="submit", variant="primary")
            yield Button("Cancel", id="back")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def submit(self) -> None:
        self.dismiss(self.query_one(Input).value)

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            self.submit()
        else:
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)


class SecretPrompt(ModalScreen[SecretStr | None]):
    BINDINGS: ClassVar[list[BindingType]] = [("escape", "cancel", "Cancel")]

    def __init__(self, label: str) -> None:
        super().__init__()
        self.label = label

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.label, markup=False)
            yield Input(password=True, id="secret", max_length=8192)
            yield Button("Continue", id="submit", variant="primary")
            yield Button("Cancel", id="back")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def submit(self) -> None:
        field = self.query_one(Input)
        value = SecretStr(field.value)
        field.value = ""
        self.dismiss(value)

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            self.submit()
        else:
            self.action_cancel()

    def action_cancel(self) -> None:
        self.query_one(Input).value = ""
        self.dismiss(None)


class Choose(ModalScreen[str | None]):
    BINDINGS: ClassVar[list[BindingType]] = [("escape", "cancel", "Cancel")]

    def __init__(self, label: str, options: list[tuple[str, str]]) -> None:
        super().__init__()
        self.label = label
        self.options = options

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.label, markup=False)
            yield Select[str](self.options, prompt=self.label)
            yield Button("Continue", id="submit")
            yield Button("Cancel", id="back")

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        value = self.query_one(Select).value
        self.dismiss(value if event.button.id == "submit" and isinstance(value, str) else None)

    def action_cancel(self) -> None:
        self.dismiss(None)
