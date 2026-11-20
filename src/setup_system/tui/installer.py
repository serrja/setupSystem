"""Two persistent sections backed by one App-owned operation and immutable snapshots."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import ClassVar, Literal, Protocol, cast

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

from setup_system.application.installer import InstallationService, InstallRequest, Operation
from setup_system.diagnostics import error_text
from setup_system.domain.settings import Section, SystemSettings, ThemeSettings, UserSettings
from setup_system.infrastructure.config_store import ConfigStore, SettingsPaths
from setup_system.infrastructure.theme import palette, preview


class InstallationPort(Protocol):
    def run(self, request: InstallRequest) -> int: ...


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
    def __init__(self, code: int) -> None:
        super().__init__()
        self.code = code


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
        self, root: Path, request: InstallRequest, *, service: InstallationPort | None = None
    ) -> None:
        super().__init__()
        self.root, self.request = root, request
        self.interaction = TextualInteraction(self)
        self.service = (
            service
            if service is not None
            else InstallationService(
                root, interaction=self.interaction, authorize=self.authenticate
            )
        )
        self.busy, self.last_code, self.quit_pending = False, 0, False
        self.cancel_requested = Event()
        self.output_lines: list[str] = []
        self.settings_revision: str | None = None
        self.system_revision: str | None = None
        self.settings = request.settings
        self.system = request.system
        self.running_request: InstallRequest | None = None
        colors = palette()
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
                yield Select(
                    [("US + RU", "us,ru"), ("US", "us")],
                    value=self.system.keyboard,
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
                yield Label(preview(self.settings.theme), id="theme-preview")
                for name, title in (
                    ("neovim", "Neovim"),
                    ("developer_tools", "Git / gh / shell tools"),
                    ("codex", "Codex CLI"),
                    ("chatgpt", "Official ChatGPT Desktop"),
                    ("firefox", "Firefox ESR + uBlock Origin"),
                    ("happ", "Happ (optional)"),
                ):
                    yield Checkbox(
                        title, value=bool(getattr(self.settings, name)), id=name.replace("_", "-")
                    )
                yield Checkbox(
                    "Python: Ruff + basedpyright",
                    value="python" in self.settings.language_profiles,
                    id="python-tools",
                )
                for profile in ("typescript_vue", "dart_flutter", "cpp"):
                    yield Checkbox(
                        profile.replace("_", " / ") + " (delivery unavailable)",
                        value=profile in self.settings.language_profiles,
                        id="language-" + profile.replace("_", "-"),
                    )
                for name in ("obsidian", "telegram", "docker", "libreoffice"):
                    yield Checkbox(
                        name.title() + " (delivery unavailable)",
                        value=name in self.settings.optional_apps,
                        id="optional-" + name,
                    )
                yield Label("Optional cleanup (nothing selected by default)")
                from setup_system.infrastructure.cleanup import cleanup_catalog

                for name in sorted(cleanup_catalog()):
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
        if not isinstance(self.service, InstallationService):
            return
        try:
            self.settings, self.settings_revision = ConfigStore(
                SettingsPaths().user, UserSettings
            ).read()
            draft = SettingsPaths().user.parent / "system-intentions.toml"
            if draft.exists():
                self.system, self.system_revision = ConfigStore(draft, SystemSettings).read()
            else:
                self.system, _ = ConfigStore(SettingsPaths().system, SystemSettings, uid=0).read()
            for name in ("neovim", "developer_tools", "codex", "chatgpt", "firefox", "happ"):
                self.query_one("#" + name.replace("_", "-"), Checkbox).value = bool(
                    getattr(self.settings, name)
                )
            self.query_one("#python-tools", Checkbox).value = (
                "python" in self.settings.language_profiles
            )
            for profile in ("typescript_vue", "dart_flutter", "cpp"):
                self.query_one("#language-" + profile.replace("_", "-"), Checkbox).value = (
                    profile in self.settings.language_profiles
                )
            for name in ("obsidian", "telegram", "docker", "libreoffice"):
                self.query_one("#optional-" + name, Checkbox).value = (
                    name in self.settings.optional_apps
                )
            from setup_system.infrastructure.cleanup import cleanup_catalog

            for name in cleanup_catalog():
                self.query_one("#cleanup-" + name.replace(".", "-"), Checkbox).value = (
                    name in self.settings.cleanup
                )
            self.query_one("#hyprland", Checkbox).value = self.system.hyprland
            self.query_one("#graphical-login", Checkbox).value = self.system.graphical_login
            self.query_one("#plymouth", Checkbox).value = self.system.plymouth
            self.query_one("#timezone", Input).value = self.system.timezone or ""
            self.query_one("#keyboard", Select).value = self.system.keyboard
            self.query_one("#compact-panel", Checkbox).value = self.settings.theme.compact_panel
            self.query_one("#effects", Checkbox).value = self.settings.theme.effects
            self.query_one("#opacity", Input).value = str(self.settings.theme.opacity)
            self.query_one("#rounding", Input).value = str(self.settings.theme.rounding)
            self.query_one("#theme-preview", Label).update(preview(self.settings.theme))
        except (OSError, ValueError) as exc:
            self.notify("Settings require review: " + error_text(exc), severity="error")

    @on(Input.Changed, "#opacity")
    @on(Input.Changed, "#rounding")
    @on(Checkbox.Changed, "#compact-panel")
    @on(Checkbox.Changed, "#effects")
    def update_preview(self) -> None:
        try:
            selected = self.snapshot(Operation.PLAN).settings.theme
            self.query_one("#theme-preview", Label).update(preview(selected))
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
        from setup_system.infrastructure.cleanup import cleanup_catalog

        settings = self.settings.model_copy(
            update={
                "theme": theme,
                **{
                    name: checked(name.replace("_", "-"))
                    for name in ("neovim", "developer_tools", "codex", "chatgpt", "firefox", "happ")
                },
                "language_profiles": tuple(
                    profile
                    for profile, enabled in (
                        ("python", checked("python-tools")),
                        *(
                            (name, checked("language-" + name.replace("_", "-")))
                            for name in ("typescript_vue", "dart_flutter", "cpp")
                        ),
                    )
                    if enabled
                ),
                "optional_apps": tuple(
                    name
                    for name in ("obsidian", "telegram", "docker", "libreoffice")
                    if checked("optional-" + name)
                ),
                "cleanup": tuple(
                    name
                    for name in sorted(cleanup_catalog())
                    if checked("cleanup-" + name.replace(".", "-"))
                ),
            }
        )
        system = SystemSettings(
            hyprland=checked("hyprland"),
            graphical_login=checked("graphical-login"),
            timezone=self.query_one("#timezone", Input).value.strip() or None,
            keyboard=cast(Literal["us", "us,ru"], self.query_one("#keyboard", Select).value),
            plymouth=checked("plymouth"),
        )
        section = Section(self.query_one("#sections", TabbedContent).active)
        return replace(
            self.request,
            operation=operation,
            section=section,
            system=system,
            settings=settings,
            yes=False,
        )

    @on(Button.Pressed, "#tools")
    def tools(self) -> None:
        from setup_system.application.tools import ToolRequest
        from setup_system.tui.tools import ToolsScreen

        def selected(request: ToolRequest | None) -> None:
            if request is not None:
                if self.busy:
                    self.notify("Another operation is running; catalog viewing remains available.")
                    return
                self.busy = True
                self.query_one("#apply", Button).disabled = True
                self.execute_tool(request)

        self.push_screen(ToolsScreen(), selected)

    @work(thread=True, exclusive=True, exit_on_error=False)
    def execute_tool(self, request: object) -> None:
        from setup_system.application.tools import MUTATING_TOOLS, ToolRequest, execute

        assert isinstance(request, ToolRequest)
        try:
            confirmed = request.action not in MUTATING_TOOLS or self.interaction.confirm(
                "Apply workspace tool: " + request.action + "?"
            )
            if not confirmed:
                self.post_message(Finished(2))
                return
            if request.action == "open_project":

                def open_editor() -> str:
                    with self.suspend():
                        return execute(self.root, request, confirmed=True)

                result = self.call_from_thread(open_editor)
            else:
                result = execute(self.root, request, confirmed=confirmed)
            self.post_message(Output(result))
            code = 0
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            self.post_message(Output(error_text(exc)))
            code = 5
        self.post_message(Finished(code))

    @on(Button.Pressed, "#save")
    def save(self) -> None:
        try:
            request = self.snapshot(Operation.PLAN)
            path = SettingsPaths().user
            self.settings_revision = ConfigStore(path, UserSettings).save(
                request.settings, self.settings_revision
            )
            self.system_revision = ConfigStore(
                path.parent / "system-intentions.toml", SystemSettings
            ).save(request.system, self.system_revision)
            self.settings = request.settings
            self.system = request.system
            self.query_one("#status", Label).update("Intentions saved; no system changes applied.")
        except (OSError, ValueError) as exc:
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
        if isinstance(self.service, InstallationService):
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
            if operation == Operation.PLAN and isinstance(self.service, InstallationService):
                try:
                    self.query_one("#output", RichLog).write(
                        "\n".join(self.service.plan(self.snapshot(operation)).describe())
                    )
                except (ValueError, OSError) as exc:
                    self.notify(error_text(exc), severity="error")
            return
        try:
            request = self.snapshot(operation)
        except (ValueError, OSError) as exc:
            self.notify(error_text(exc), severity="error")
            return
        self.cancel_requested.clear()
        self.busy = True
        self.running_request = request
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
            code = 5
        self.post_message(Finished(code))

    def authenticate(self) -> bool:
        """Sudo reads its password from the terminal; Python never receives it."""

        def run() -> bool:
            with self.suspend():
                return subprocess.run(["/usr/bin/sudo", "-v"], check=False).returncode == 0

        return self.call_from_thread(run)

    @on(Output)
    def output(self, message: Output) -> None:
        self.output_lines.append(message.text)
        self.query_one("#output", RichLog).write(message.text)

    @on(Finished)
    def finished(self, message: Finished) -> None:
        self.last_code, self.busy = message.code, False
        self.query_one("#apply", Button).disabled = False
        self.query_one("#stop", Button).disabled = True
        self.query_one("#status", Label).update(
            "Completed (exit 0)"
            if message.code == 0
            else f"Stopped (exit {message.code}); completed stages remain visible."
        )
        if self.quit_pending:
            self.exit(message.code)
