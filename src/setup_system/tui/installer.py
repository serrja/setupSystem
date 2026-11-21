"""Two persistent sections backed by one App-owned operation and immutable snapshots."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from threading import Event
from typing import ClassVar, Literal, cast

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    TabbedContent,
    TabPane,
)

from setup_system.application.ui_ports import InstallationPort, ViewServices
from setup_system.diagnostics import PartialApplyError, error_text
from setup_system.domain.displays import MonitorLayout
from setup_system.domain.operations import InstallRequest, Operation
from setup_system.domain.settings import Section, SystemSettings, ThemeSettings
from setup_system.domain.tool_requests import ToolRequest, mutates


class Prompt(ModalScreen[str | None]):
    AUTO_FOCUS: ClassVar[str | None] = "#confirm"
    DEFAULT_CSS = """
    Prompt { align: center middle; }
    Prompt > Vertical { width: 85%; height: 85%; border: round $accent; padding: 1 2; }
    Prompt Horizontal { height: auto; }
    #review { height: 1fr; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, prompt: str, *, plan: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.prompt, self.plan = prompt, plan

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.prompt)
            yield RichLog(id="review", markup=False, highlight=False, wrap=True)
            with Horizontal():
                yield Button("Apply snapshot", id="confirm", variant="success")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#review", RichLog).write("\n".join(self.plan))

    @on(Button.Pressed, "#confirm")
    def accept(self) -> None:
        self.dismiss("yes")

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class Output(Message):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class Finished(Message):
    def __init__(self, code: int, status: str | None = None) -> None:
        super().__init__()
        self.code, self.status = code, status


class TextualInteraction:
    def __init__(self, app: InstallationApp) -> None:
        self.app = app

    @property
    def cancelled(self) -> bool:
        return self.app.cancel_requested.is_set() or not self.app.is_running

    @property
    def interactive(self) -> bool:
        return True

    def write(
        self, text: str, *, end: str = "\n", error: bool = False, flush: bool = False
    ) -> None:
        self.app.post_message(Output(text.rstrip("\n")))

    def confirm(self, prompt: str) -> bool:
        ready = Event()
        result: list[str | None] = []

        def answered(value: str | None) -> None:
            result.append(value)
            ready.set()

        def show() -> None:
            header, _, detail = prompt.partition("\n")
            self.app.push_screen(Prompt(header, plan=tuple(detail.splitlines())), answered)

        self.app.call_from_thread(show)
        while not ready.wait(0.1):
            if self.cancelled:
                return False
        return result[0] == "yes"


class InstallationApp(App[int]):
    TITLE = "setupSystem"
    SUB_TITLE = "Debian 13 · Catppuccin Mocha Green"
    AUTO_FOCUS: ClassVar[str | None] = "#sections"
    CSS = """
    Screen { background: $background; color: $text; }
    #sections { height: 45%; min-height: 8; }
    #actions { height: auto; padding: 0 1; }
    #actions Button { min-width: 8; width: auto; margin-right: 1; }
    #status { height: auto; padding: 0 1; }
    #output { border: round $accent; margin: 0 1; height: 1fr; min-height: 3; }
    Input { height: 3; }
    Checkbox { height: 3; }
    .settings-row { height: auto; }
    #theme-preview { height: auto; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c,ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+1", "section('base')", "Base system"),
        Binding("ctrl+2", "section('workspace')", "Workspace"),
    ]

    def __init__(
        self, request: InstallRequest, *, service: InstallationPort, views: ViewServices
    ) -> None:
        super().__init__()
        self.request, self.service, self.views = request, service, views
        self.interaction = TextualInteraction(self)
        self.busy, self.last_code, self.quit_pending = False, 0, False
        self.cancel_requested = Event()
        self.output_lines: list[str] = []
        self.settings_revision: str | None = None
        self.system_revision: str | None = None
        self.root_revision: str | None = None
        self.revisions_loaded = False
        self.settings = request.settings
        self.system = request.system
        self.running_request: InstallRequest | None = None
        colors = views.palette
        self.register_theme(
            Theme(
                name="mocha-green",
                primary=colors["green"],
                secondary=colors["teal"],
                accent=colors["green"],
                foreground=colors["text"],
                background=colors["base"],
                surface=colors["mantle"],
                panel=colors["surface0"],
                error=colors["red"],
                warning=colors["yellow"],
                success=colors["green"],
                dark=True,
            )
        )
        self.theme = "mocha-green"

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="base", id="sections"):
            with TabPane("Базовая система", id="base"), VerticalScroll():
                yield Checkbox(
                    "Hyprland and base components", value=self.system.hyprland, id="hyprland"
                )
                yield Checkbox(
                    "Graphical login (greetd / gtkgreet)",
                    value=self.system.graphical_login,
                    id="graphical-login",
                )
                yield Input(
                    value=self.system.timezone or "",
                    placeholder="Timezone (empty preserves current)",
                    id="timezone",
                )
                yield Input(
                    value=self.system.locale or "",
                    placeholder="System locale (e.g. en_US.UTF-8, empty preserves current)",
                    id="locale",
                )
                yield Select(
                    [("Keep keyboard layout", ""), ("US + RU", "us,ru"), ("US", "us")],
                    value=self.system.keyboard or "",
                    allow_blank=False,
                    id="keyboard",
                )
                yield Checkbox(
                    "Plymouth native unlock theme (requires boot recovery validation)",
                    value=self.system.plymouth,
                    id="plymouth",
                )
                yield Label("Existing network, SSH, disks and native unlock are preserved.")
            with TabPane("Рабочее окружение", id="workspace"), VerticalScroll():
                yield Button("Configure connected displays", id="monitors")
                yield Checkbox(
                    "Apply desktop theme", value=self.settings.desktop_theme, id="desktop-theme"
                )
                yield Select(
                    [("Catppuccin Mocha Green", "catppuccin_mocha_green")],
                    value="catppuccin_mocha_green",
                    allow_blank=False,
                    id="theme",
                )
                with Horizontal(classes="settings-row"):
                    yield Checkbox(
                        "Compact panel", value=self.settings.theme.compact_panel, id="compact-panel"
                    )
                    yield Checkbox("Effects", value=self.settings.theme.effects, id="effects")
                yield Input(
                    value=str(self.settings.theme.opacity),
                    placeholder="Opacity: 0.75-1.00",
                    id="opacity",
                )
                yield Input(
                    value=str(self.settings.theme.rounding),
                    placeholder="Rounding: 0-24",
                    id="rounding",
                )
                yield Label(self.views.preview(self.settings.theme), id="theme-preview")
                selected = {
                    *self.settings.apps,
                    *self.settings.optional_apps,
                    *self.settings.language_profiles,
                }
                for entry in self.views.catalog.components():
                    title = (
                        entry.title if entry.blocker is None else f"{entry.title} · {entry.blocker}"
                    )
                    yield Checkbox(title, value=entry.id in selected, id=entry.id.replace("_", "-"))
                    attempt = self.views.last_attempt(entry.id)
                    if attempt:
                        yield Label(f"Last download: {attempt}", markup=False)
                yield Select(
                    [
                        ("Remove packages", "remove"),
                        ("Purge package settings", "purge"),
                        ("Review automatic removals", "autoremove"),
                    ],
                    value=self.settings.cleanup_mode,
                    id="cleanup-mode",
                    allow_blank=False,
                )
                yield Label("Optional cleanup (nothing selected by default)")
                for name in self.views.cleanup:
                    yield Checkbox(
                        name,
                        value=name in self.settings.cleanup,
                        id="cleanup-" + name.replace(".", "-"),
                    )
        with Horizontal(id="actions"):
            yield Button("Plan", id="plan", variant="primary")
            yield Button("Apply", id="apply", variant="success")
            yield Button("Save intentions", id="save")
            yield Button("Tools", id="tools")
            yield Button("Cancel", id="stop", disabled=True)
            yield Button("Quit", id="quit")
        yield Label("Read-only until you explicitly confirm Apply.", id="status")
        yield RichLog(id="output", markup=False, highlight=False, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        try:
            saved = self.views.settings.read()
            self.settings, self.settings_revision = saved.user, saved.user_revision
            self.system, self.system_revision = saved.system, saved.draft_revision
            self.root_revision = saved.system_revision
            if saved.warning:
                self.notify(saved.warning, severity="warning")
            self.revisions_loaded = True
            selected = {
                *self.settings.apps,
                *self.settings.optional_apps,
                *self.settings.language_profiles,
            }
            for entry in self.views.catalog.components():
                self.query_one("#" + entry.id.replace("_", "-"), Checkbox).value = (
                    entry.id in selected
                )
            self.query_one("#desktop-theme", Checkbox).value = self.settings.desktop_theme
            self.query_one("#cleanup-mode", Select).value = self.settings.cleanup_mode
            for name in self.views.cleanup:
                self.query_one("#cleanup-" + name.replace(".", "-"), Checkbox).value = (
                    name in self.settings.cleanup
                )
            self.query_one("#hyprland", Checkbox).value = self.system.hyprland
            self.query_one("#graphical-login", Checkbox).value = self.system.graphical_login
            self.query_one("#plymouth", Checkbox).value = self.system.plymouth
            self.query_one("#timezone", Input).value = self.system.timezone or ""
            self.query_one("#locale", Input).value = self.system.locale or ""
            self.query_one("#keyboard", Select).value = self.system.keyboard or ""
            self.query_one("#compact-panel", Checkbox).value = self.settings.theme.compact_panel
            self.query_one("#effects", Checkbox).value = self.settings.theme.effects
            self.query_one("#opacity", Input).value = str(self.settings.theme.opacity)
            self.query_one("#rounding", Input).value = str(self.settings.theme.rounding)
            self.query_one("#theme-preview", Label).update(self.views.preview(self.settings.theme))
        except (OSError, ValueError) as exc:
            self.query_one("#apply", Button).disabled = True
            self.query_one("#save", Button).disabled = True
            self.notify("Settings require review: " + error_text(exc), severity="error")

    @on(Input.Changed, "#opacity")
    @on(Input.Changed, "#rounding")
    @on(Checkbox.Changed, "#compact-panel")
    @on(Checkbox.Changed, "#effects")
    def update_preview(self) -> None:
        try:
            selected = self.snapshot(Operation.PLAN).settings.theme
            self.query_one("#theme-preview", Label).update(self.views.preview(selected))
        except (OSError, ValueError):
            pass

    def action_section(self, section: str) -> None:
        self.query_one("#sections", TabbedContent).active = section

    def snapshot(self, operation: Operation) -> InstallRequest:
        def checked(name: str) -> bool:
            return self.query_one("#" + name, Checkbox).value

        theme = ThemeSettings(
            compact_panel=checked("compact-panel"),
            effects=checked("effects"),
            opacity=float(self.query_one("#opacity", Input).value),
            rounding=int(self.query_one("#rounding", Input).value),
        )
        settings = self.settings.validated(
            **{
                "theme": theme,
                "desktop_theme": checked("desktop-theme"),
                **{
                    field: tuple(
                        e.id
                        for e in self.views.catalog.components(kind)
                        if checked(e.id.replace("_", "-"))
                    )
                    for field, kind in (
                        ("apps", "app"),
                        ("language_profiles", "language"),
                        ("optional_apps", "optional"),
                    )
                },
                "cleanup_mode": self.query_one("#cleanup-mode", Select).value,
                "cleanup": tuple(
                    name
                    for name in self.views.cleanup
                    if checked("cleanup-" + name.replace(".", "-"))
                ),
            }
        )
        system = SystemSettings(
            hyprland=checked("hyprland"),
            graphical_login=checked("graphical-login"),
            timezone=self.query_one("#timezone", Input).value.strip() or None,
            locale=self.query_one("#locale", Input).value.strip() or None,
            keyboard=cast(
                Literal["us", "us,ru"] | None, self.query_one("#keyboard", Select).value or None
            ),
            plymouth=checked("plymouth"),
        )
        section = Section(self.query_one("#sections", TabbedContent).active)
        self.views.catalog.validate_settings(settings)
        return self.request.validated(
            operation=operation,
            section=section,
            system=system,
            settings=settings,
            revision_known=self.revisions_loaded,
            system_revision=self.root_revision,
            draft_revision=self.system_revision,
            user_revision=self.settings_revision,
            yes=False,
        )

    @on(Button.Pressed, "#monitors")
    def monitors(self) -> None:
        from setup_system.tui.displays import DisplaysScreen

        if self.busy:
            self.notify("Wait for the running operation before previewing a display change.")
            return
        try:
            outputs = self.views.displays.discover()
            if not outputs:
                raise ValueError("No connected Hyprland displays")
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            self.notify(error_text(exc), severity="warning")
            return

        def selected(layouts: tuple[MonitorLayout, ...] | None) -> None:
            if layouts is not None:
                self.settings = self.settings.validated(monitors=layouts)
                self.query_one("#desktop-theme", Checkbox).value = True

        self.push_screen(
            DisplaysScreen(outputs, self.settings.monitors, self.views.displays), selected
        )

    @on(Button.Pressed, "#tools")
    def tools(self) -> None:
        from setup_system.tui.tools import ToolsScreen

        def selected(request: ToolRequest | None) -> None:
            if request is not None:
                if self.busy:
                    self.notify("Another operation is running; catalog viewing remains available.")
                    return
                self.busy = True
                self.query_one("#save", Button).disabled = True
                self.query_one("#apply", Button).disabled = True
                self.execute_tool(request)

        try:
            self.push_screen(ToolsScreen(self.views.tools.choices()), selected)
        except (ValueError, OSError, RuntimeError) as exc:
            self.notify(error_text(exc), severity="error")

    @work(thread=True, exclusive=True, exit_on_error=False)
    def execute_tool(self, request: ToolRequest) -> None:
        try:
            confirmed = True
            if mutates(request):
                details = self.views.tools.describe(request)
                confirmed = self.interaction.confirm(
                    "\n".join([request.action.replace("_", " ").capitalize() + "?", *details])
                )
            if not confirmed:
                self.post_message(Finished(2))
                return
            if request.action in {"open_project", "open_codex"}:
                result = self.with_terminal(
                    lambda: self.views.tools.execute(request, confirmed=True)
                )
            else:
                result = self.views.tools.execute(request, confirmed=confirmed)
            self.post_message(Output(result))
            code = 0
        except PartialApplyError as exc:
            self.post_message(Output(error_text(exc)))
            self.post_message(Finished(5, "Partial"))
            return
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            self.post_message(Output(error_text(exc)))
            code = 5
        self.post_message(Finished(code))

    @on(Button.Pressed, "#save")
    def save(self) -> None:
        if self.busy:
            self.notify("Finish the running operation before saving intentions.")
            return
        try:
            request = self.snapshot(Operation.PLAN)
            self.settings_revision, self.system_revision = self.views.settings.save(
                request.settings, request.system, self.settings_revision, self.system_revision
            )
            self.settings = request.settings
            self.system = request.system
            self.query_one("#status", Label).update("Intentions saved; no system changes applied.")
        except (OSError, ValueError, RuntimeError) as exc:
            self.notify(error_text(exc), severity="error")

    @on(Button.Pressed, "#plan")
    def plan(self) -> None:
        self._start(Operation.PLAN)

    @on(Button.Pressed, "#apply")
    def apply(self) -> None:
        self._start(Operation.APPLY)

    @on(Button.Pressed, "#stop")
    def stop(self) -> None:
        self.cancel_requested.set()
        self.service.cancel.set()
        self.query_one("#status", Label).update(
            "Cancellation requested; waiting for a safe boundary."
        )

    @on(Button.Pressed, "#quit")
    async def action_quit(self) -> None:
        if self.busy:
            self.quit_pending = True
            self.stop()
        else:
            self.exit(self.last_code)

    def _start(self, operation: Operation) -> None:
        if self.busy:
            if operation == Operation.PLAN:
                try:
                    self.query_one("#output", RichLog).write(
                        "\n".join(self.service.plan(self.snapshot(operation)).describe())
                    )
                except (ValueError, OSError) as exc:
                    self.notify(error_text(exc), severity="error")
            return
        try:
            if operation == Operation.APPLY:
                if not self.revisions_loaded:
                    raise ValueError("Reload valid settings before applying changes")
                self.views.settings.check_draft(self.system_revision)
            request = self.snapshot(operation)
        except (ValueError, OSError) as exc:
            self.notify(error_text(exc), severity="error")
            return
        self.cancel_requested.clear()
        self.busy = True
        self.running_request = request
        self.query_one("#save", Button).disabled = True
        self.query_one("#apply", Button).disabled = True
        self.query_one("#stop", Button).disabled = False
        self.query_one("#status", Label).update(
            "Running " + operation.value + "; navigation remains available"
        )
        self.execute(request)

    @work(thread=True, exclusive=True, exit_on_error=False)
    def execute(self, request: InstallRequest) -> None:
        try:
            code = self.service.run(request)
        except Exception as exc:
            self.post_message(Output("FAILED: " + error_text(exc)))
            self.post_message(Finished(5, "Failed"))
            return
        status = self.service.status
        self.post_message(
            Finished(code, status.value.replace("_", " ").capitalize() if status else None)
        )

    def with_terminal[T](self, callback: Callable[[], T]) -> T:
        def run() -> T:
            with self.suspend():
                return callback()

        return self.call_from_thread(run)

    @on(Output)
    def output(self, message: Output) -> None:
        self.output_lines.append(message.text)
        self.query_one("#output", RichLog).write(message.text)

    @on(Finished)
    def finished(self, message: Finished) -> None:
        self.last_code, self.busy = message.code, False
        if (
            self.running_request is not None
            and self.running_request.operation == Operation.APPLY
            and "system_settings" in self.service.completed
        ):
            try:
                stored, revision = self.views.settings.system()
                if stored != self.running_request.system:
                    raise ValueError("System settings changed after Apply. Reopen the application.")
                self.root_revision = revision
            except (OSError, ValueError) as exc:
                self.revisions_loaded = False
                self.notify(error_text(exc), severity="error")
        valid = self.revisions_loaded
        self.query_one("#save", Button).disabled = not valid
        self.query_one("#apply", Button).disabled = not valid
        self.query_one("#stop", Button).disabled = True
        summary = {
            0: "Complete",
            2: "Cancelled",
            3: "Cancelled at a safe boundary",
            4: "Blocked",
            5: "Failed",
        }
        self.query_one("#status", Label).update(
            message.status or summary.get(message.code, "Stopped")
        )
        self.running_request = None
        if self.quit_pending:
            self.exit(message.code)
