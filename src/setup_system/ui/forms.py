"""The two persistent Textual forms and their typed selections."""

from __future__ import annotations

from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.suggester import SuggestFromList
from textual.widgets import Button, Checkbox, Input, Label, Select, SelectionList, Static

from setup_system.domain.intentions import CleanupMode
from setup_system.models import BaseOptions, Monitor, Theme, WorkspaceOptions
from setup_system.suggestions import Catalog, KeyboardChoices, ProjectChoices, Suggestions


class CompletionInput(Input):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("tab", "complete_or_next", show=False),
        Binding("escape", "hide_suggestion", show=False),
    ]

    def accept(self) -> bool:
        if (
            self.cursor_position == len(self.value)
            and self._suggestion
            and self._suggestion != self.value
        ):
            self.action_cursor_right()
            return True
        return False

    def action_complete_or_next(self) -> None:
        if not self.accept():
            self.screen.focus_next()

    def action_hide_suggestion(self) -> None:
        self._suggestion = ""

    async def action_submit(self) -> None:
        if not self.accept():
            await super().action_submit()


class BaseForm(VerticalScroll):
    def __init__(self, suggestions: Suggestions):
        super().__init__()
        self.suggestions = suggestions

    def compose(self) -> ComposeResult:
        yield Checkbox("Hyprland, sound, portals and desktop dependencies", id="hyprland")
        yield Checkbox("Graphical login", id="graphical_login")
        yield Checkbox("Plymouth graphical unlock", id="plymouth")
        for key, label in (
            ("timezone", "Timezone"),
            ("locale", "Locale"),
            ("keyboard", "Keyboard layouts"),
        ):
            yield Label(label)
            yield CompletionInput(id=key, placeholder="Keep current")

        yield Label("Local account for administrative access")
        yield Input(id="admin-account", placeholder="Existing local username")
        yield Button("Review sudo access", id="admin-plan")

    def refresh_choices(self) -> None:
        self.query_one("#timezone", Input).suggester = SuggestFromList(self.suggestions.timezones)
        self.query_one("#locale", Input).suggester = SuggestFromList(self.suggestions.locales)
        self.query_one("#keyboard", Input).suggester = KeyboardChoices(self.suggestions.keyboards)

    def populate(self, options: BaseOptions) -> None:
        for key in ("hyprland", "graphical_login", "plymouth"):
            self.query_one("#" + key, Checkbox).value = getattr(options, key)
        for key in ("timezone", "locale", "keyboard"):
            self.query_one("#" + key, Input).value = getattr(options, key) or ""

    def options(self) -> BaseOptions:
        values = {
            key: self.query_one("#" + key, Checkbox).value
            for key in ("hyprland", "graphical_login", "plymouth")
        }
        fields = {
            key: self.query_one("#" + key, Input).value or None
            for key in ("timezone", "locale", "keyboard")
        }
        return BaseOptions.model_validate(values | fields)


class WorkspaceForm(VerticalScroll):
    def __init__(self, catalog: Catalog, suggestions: Suggestions):
        super().__init__()
        self.catalog, self.suggestions = catalog, suggestions
        self.monitors: dict[str, Monitor] = {}
        self.path_choices = ProjectChoices(suggestions)

    def compose(self) -> ComposeResult:
        yield Checkbox("Apply desktop theme and top panel", id="desktop_theme")
        yield Checkbox("Compact panel", id="compact_panel")
        yield Checkbox("Compositor effects", id="effects")
        yield Label("Window opacity")
        yield Input(id="opacity")
        yield Label("Corner radius (px)")
        yield Input(id="rounding")
        yield Label("Connected display")
        yield CompletionInput(id="output")
        yield Label("Advertised mode")
        yield CompletionInput(id="mode")
        for key, label in (
            ("scale", "Scale"),
            ("x", "Horizontal position"),
            ("y", "Vertical position"),
        ):
            yield Label(label)
            yield Input(str(Monitor.model_fields[key].default), id=key)
        with Horizontal(classes="buttons"):
            yield Button("Set layout", id="set-layout")
            yield Button("Forget layout", id="forget-layout")
            yield Button("Test for 12 seconds", id="display-preview")
        yield Static(id="layouts", markup=False)
        yield Label("Applications")
        yield SelectionList(*((title, key) for key, title in self.catalog.apps.items()), id="apps")
        yield Label("Language profiles")
        yield SelectionList(
            *((title, key) for key, title in self.catalog.languages.items()), id="languages"
        )
        yield Label("Existing project directory")
        yield CompletionInput(id="project", suggester=self.path_choices)
        yield Button("Check directory", id="project-check")
        with Horizontal(classes="buttons"):
            yield Button("Catalog projects", id="manage-projects")
            yield Button("Project templates", id="manage-templates")
            yield Button("Git profiles", id="manage-git")
        with Horizontal(classes="buttons"):
            yield Button("Preview shell setup", id="shell-configure-plan")
            yield Button("Preview RTK install", id="rtk-install-plan")
        yield Label("Optional removal candidates")
        yield SelectionList(*((name, name) for name in self.catalog.cleanup), id="cleanup")
        yield Select(
            [(mode.value, mode) for mode in CleanupMode],
            allow_blank=False,
            id="cleanup-mode",
        )
        yield Button("Plan cleanup", id="cleanup-plan")
        yield Label("Recovery backup")
        yield CompletionInput(id="backup")
        yield Button("Plan restore", id="restore-plan")

    @on(Input.Changed, "#output")
    def output_changed(self, event: Input.Changed) -> None:
        self.refresh_modes()
        field = self.query_one("#mode", CompletionInput)
        saved = self.monitors.get(event.value)
        field.value = saved.mode if saved else ""
        if saved:
            for key in ("scale", "x", "y"):
                self.query_one("#" + key, Input).value = str(getattr(saved, key))

    def refresh_choices(self) -> None:
        self.query_one("#output", Input).suggester = SuggestFromList(self.suggestions.modes)
        self.query_one("#backup", Input).suggester = SuggestFromList(self.suggestions.backups)
        self.refresh_modes()

    def refresh_modes(self) -> None:
        output = self.query_one("#output", Input).value
        mode = self.query_one("#mode", CompletionInput)
        mode.suggester = SuggestFromList(self.suggestions.modes.get(output, ()))
        mode.action_hide_suggestion()

    def project_path(self) -> str | None:
        value = self.query_one("#project", Input).value
        if value.startswith("~/"):
            value = f"{self.suggestions.home}{value[1:]}"
        return value or None

    def update_layout(self, remove: bool) -> None:
        output = self.query_one("#output", Input).value
        if remove:
            self.monitors.pop(output, None)
        else:
            mode = self.query_one("#mode", Input).value
            if mode not in self.suggestions.modes.get(output, ()):
                raise ValueError(
                    "Inspect connected displays and choose a mode advertised by that output"
                )
            self.monitors[output] = Monitor(
                output=output,
                mode=mode,
                scale=float(self.query_one("#scale", Input).value),
                x=int(self.query_one("#x", Input).value),
                y=int(self.query_one("#y", Input).value),
            )
        self.show_layouts()

    def show_layouts(self) -> None:
        self.query_one("#layouts", Static).update(
            "\n".join(
                f"{m.output}  {m.mode}  {m.x},{m.y}  scale {m.scale:g}"
                for m in self.monitors.values()
            )
        )

    def populate(self, options: WorkspaceOptions) -> None:
        for key in ("apps", "languages", "cleanup"):
            values = getattr(options, key)
            unknown = set(values).difference(getattr(self.catalog, key))
            if unknown:
                raise ValueError(f"Unknown {key} selections: {', '.join(sorted(unknown))}")
            widget = self.query_one("#" + key, SelectionList)
            widget.deselect_all()
            for value in values:
                widget.select(value)
        self.query_one("#desktop_theme", Checkbox).value = options.desktop_theme
        for key in ("compact_panel", "effects"):
            self.query_one("#" + key, Checkbox).value = getattr(options.theme, key)
        for key in ("opacity", "rounding"):
            self.query_one("#" + key, Input).value = str(getattr(options.theme, key))
        self.query_one("#cleanup-mode", Select).value = options.cleanup_mode
        self.query_one("#project", Input).value = options.project or ""
        self.monitors = {m.output: m for m in options.monitors}
        self.show_layouts()

    def options(self) -> WorkspaceOptions:
        return WorkspaceOptions.model_validate(
            {
                "desktop_theme": self.query_one("#desktop_theme", Checkbox).value,
                "theme": Theme(
                    compact_panel=self.query_one("#compact_panel", Checkbox).value,
                    effects=self.query_one("#effects", Checkbox).value,
                    opacity=float(self.query_one("#opacity", Input).value),
                    rounding=int(self.query_one("#rounding", Input).value),
                ),
                "monitors": tuple(self.monitors.values()),
                **{
                    key: self.query_one("#" + key, SelectionList).selected
                    for key in ("apps", "languages", "cleanup")
                },
                "cleanup_mode": self.query_one("#cleanup-mode", Select).value,
                "project": self.project_path(),
            }
        )
