"""Terminal installation UI; system and user execution belong to the application service."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import ClassVar, Protocol

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, RichLog

from setup_system.application.installer import InstallRequest, Operation
from setup_system.application.interaction import Interaction
from setup_system.diagnostics import error_text
from setup_system.tui.process import ProcessInstallation


class InstallationPort(Protocol):
    def run(self, request: InstallRequest) -> int: ...


class Prompt(ModalScreen[str | None]):
    """One explicit confirmation or secret input; secrets never enter the log widget."""

    AUTO_FOCUS: ClassVar[str | None] = "#secret, #confirm"

    DEFAULT_CSS = """
    Prompt { align: center middle; }
    Prompt > Vertical { width: 80%; height: auto; border: round $accent; padding: 1 2; }
    Prompt Horizontal { height: auto; }
    Prompt > .review-plan { height: 85%; }
    #review { height: 1fr; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, prompt: str, *, secret: bool = False, plan: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.prompt = prompt
        self.secret = secret
        self.plan = plan

    def compose(self) -> ComposeResult:
        with Vertical(classes="" if self.secret else "review-plan"):
            yield Label(self.prompt)
            if self.secret:
                yield Input(password=True, placeholder="HTTPS subscription", id="secret")
            else:
                yield RichLog(
                    id="review", markup=False, highlight=False, wrap=True, auto_scroll=False
                )
            yield Label("Tab/Shift+Tab: select; Enter: activate; Esc: cancel.")
            with Horizontal():
                yield Button(
                    "Continue" if self.secret else "Apply complete plan",
                    id="confirm",
                    variant="primary",
                )
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        if not self.secret:
            self.query_one("#review", RichLog).write("\n".join(self.plan))

    @on(Button.Pressed, "#confirm")
    def accept(self) -> None:
        value = self.query_one("#secret", Input).value if self.secret else "yes"
        if self.secret:
            self.query_one("#secret", Input).value = ""
        self.dismiss(value)

    @on(Input.Submitted)
    def submit(self) -> None:
        self.accept()

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        if self.secret:
            self.query_one("#secret", Input).value = ""
        self.dismiss(None)


class Output(Message):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class Finished(Message):
    def __init__(self, code: int) -> None:
        super().__init__()
        self.code = code


class TextualInteraction:
    """Bridge a synchronous use case to Textual's UI thread without redirecting stdio."""

    def __init__(self, app: InstallationApp) -> None:
        self.app = app

    @property
    def cancelled(self) -> bool:
        return not self.app.is_running

    @property
    def interactive(self) -> bool:
        return True

    def write(
        self, text: str, *, end: str = "\n", error: bool = False, flush: bool = False
    ) -> None:
        self.app.post_message(Output(text.rstrip("\n")))

    def _ask(self, prompt: str, *, secret: bool = False) -> str | None:
        ready = Event()
        result: list[str | None] = []

        def answered(value: str | None) -> None:
            result.append(value)
            ready.set()

        def show() -> None:
            self.app.push_screen(
                Prompt(prompt, secret=secret, plan=tuple(self.app.output_lines)), answered
            )

        self.app.call_from_thread(show)
        while not ready.wait(0.2):
            if not self.app.is_running:
                raise EOFError("Terminal interface closed")
        return result.pop()

    def confirm(self, prompt: str) -> bool:
        return self._ask(prompt) == "yes"

    def subscription(self) -> str:
        value = self._ask("HTTPS VPN subscription (not saved or logged)", secret=True)
        if value is None:
            raise EOFError("Subscription input cancelled")
        return value


class InstallationApp(App[int]):
    """Run one checked operation at a time and keep recovery diagnostics visible."""

    AUTO_FOCUS: ClassVar[str | None] = "#user"
    TITLE = "setupSystem"
    SUB_TITLE = "Debian → Hyprland"
    CSS = """
    #fields { height: auto; padding: 1 2; }
    #actions { height: auto; padding: 0 2; }
    #status { height: auto; padding: 1 2; }
    #output { border: round $accent; margin: 0 2 1 2; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c,ctrl+q", "quit", "Quit", priority=True)
    ]

    def __init__(
        self,
        root: Path,
        request: InstallRequest,
        *,
        service: InstallationPort | None = None,
    ) -> None:
        super().__init__()
        self.root = root
        self.request = request
        self.interaction: Interaction = TextualInteraction(self)
        self.service = (
            service if service is not None else ProcessInstallation(root, self.interaction)
        )
        self.busy = False
        self.last_code = 0
        self.output_lines: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="fields"):
            yield Label("Target user (leave empty to use the verified sudo caller)")
            yield Input(value=self.request.user or "", id="user")
            yield Label("Timezone (Region/City; empty preserves the current setting)")
            yield Input(value=self.request.timezone or "", id="timezone")
            yield Label("Enter in a field: read-only plan. Tab/Shift+Tab: select an action.")
            yield Label(f"Verified local source: {self.root}", markup=False)
        with Horizontal(id="actions"):
            yield Button("Read-only plan", id="plan", variant="primary")
            yield Button("Install", id="apply", variant="success")
            yield Button("Quit", id="quit")
        yield Label("Inspect the plan before installation. No changes have been made.", id="status")
        yield RichLog(id="output", markup=False, highlight=False, wrap=True)
        yield Footer()

    @on(Input.Submitted, "#user")
    def submit_user(self) -> None:
        self.plan()

    @on(Input.Submitted, "#timezone")
    def submit_timezone(self) -> None:
        self.plan()

    @on(Button.Pressed, "#plan")
    def plan(self) -> None:
        self._start(Operation.PLAN)

    @on(Button.Pressed, "#apply")
    def apply(self) -> None:
        self._start(Operation.APPLY)

    @on(Button.Pressed, "#quit")
    async def action_quit(self) -> None:
        if self.busy:
            self.notify("Wait for the current stage and its cleanup before quitting.")
            return
        self.exit(self.last_code)

    def _start(self, operation: Operation) -> None:
        if self.busy:
            return
        self.busy = True
        self.output_lines.clear()
        self.query_one("#user", Input).disabled = True
        self.query_one("#timezone", Input).disabled = True
        for button in self.query(Button):
            button.disabled = True
        self.query_one("#status", Label).update("Running " + operation.value)
        request = replace(
            self.request,
            operation=operation,
            user=self.query_one("#user", Input).value.strip() or None,
            timezone=self.query_one("#timezone", Input).value.strip() or None,
            yes=False,
        )
        self.execute(request)

    @work(thread=True, exclusive=True, exit_on_error=False)
    def execute(self, request: InstallRequest) -> None:
        try:
            code = self.service.run(request)
        except Exception as exc:
            self.post_message(Output("FAILED: " + error_text(exc)))
            code = 5
        self.post_message(Finished(code))

    @on(Output)
    def output(self, message: Output) -> None:
        self.output_lines.append(message.text)
        self.query_one("#output", RichLog).write(message.text)

    @on(Finished)
    def finished(self, message: Finished) -> None:
        self.last_code = message.code
        self.busy = False
        self.query_one("#user", Input).disabled = False
        self.query_one("#timezone", Input).disabled = False
        for button in self.query(Button):
            button.disabled = False
        self.query_one("#plan", Button).focus()
        self.query_one("#status", Label).update(
            "Completed (exit 0)"
            if message.code == 0
            else f"Stopped (exit {message.code}); review diagnostics above."
        )
