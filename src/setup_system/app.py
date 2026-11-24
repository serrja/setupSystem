"""Normal-user Textual entry point for the native Make tasks."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from contextlib import nullcontext
from typing import ClassVar, Literal

from textual import on
from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.theme import Theme as TextualTheme
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Select,
    SelectionList,
    Static,
    TabbedContent,
    TabPane,
)

from setup_system.infrastructure.palette import palette
from setup_system.models import (
    BaseOptions,
    PlanSnapshot,
    PlanTask,
    ProjectTask,
    Section,
    TaskResult,
    WorkspaceOptions,
)
from setup_system.runner import FOREGROUND, BecomePassword, MakeRunner, Target
from setup_system.settings import Loaded, Settings
from setup_system.suggestions import Catalog, Suggestions, config_root
from setup_system.ui.dialogs import Consent, Password
from setup_system.ui.forms import BaseForm, WorkspaceForm
from setup_system.ui.projects import ProjectTools, ToolChoice

Group = Literal["projects", "templates", "git"]
PROJECT_PLANS: dict[Target, ProjectTask] = {
    Target.PROJECT_PREPARE_PLAN: "project-prepare",
    Target.TEMPLATE_CACHE_PLAN: "template-cache",
    Target.TEMPLATE_CREATE_PLAN: "template-create",
    Target.TEMPLATE_UPDATE_PLAN: "template-update",
    Target.GIT_CONFIGURE_PLAN: "git-configure",
    Target.GIT_USE_PLAN: "git-use",
    Target.SHELL_CONFIGURE_PLAN: "shell-configure",
    Target.RTK_INSTALL_PLAN: "rtk-install",
}
GROUPS: dict[str, Group] = {
    "manage-projects": "projects",
    "manage-templates": "templates",
    "manage-git": "git",
}
RUN_BUTTONS = (
    "inspect",
    "plan",
    "cleanup-plan",
    "restore-plan",
    "project-check",
    "display-preview",
    "shell-configure-plan",
    "rtk-install-plan",
    *GROUPS,
)
ERRORS = (OSError, ValueError, RuntimeError)


class SetupApp(App[None]):
    TITLE = "setupSystem"
    BINDINGS: ClassVar[list[BindingType]] = [
        ("ctrl+1", "base", "Base"),
        ("ctrl+2", "workspace", "Workspace"),
        ("ctrl+q", "quit", "Quit"),
    ]
    CSS = """
    Screen { layout: vertical; }
    TabbedContent { height: 1fr; }
    BaseForm, WorkspaceForm { padding: 0 1; }
    SelectionList { height: auto; min-height: 3; max-height: 9; }
    .buttons { height: auto; }
    .buttons Button { min-width: 9; width: auto; margin-right: 1; }
    #feedback { height: auto; max-height: 9; padding: 0 1; overflow-y: auto; }
    Consent, Password { align: center middle; }
    #dialog { width: 90%; height: auto; max-height: 90%; padding: 1;
              border: solid $accent; background: $surface; }
    #review { height: auto; max-height: 12; }
    """

    def __init__(
        self,
        runner: MakeRunner | None = None,
        settings: Settings | None = None,
        catalog: Catalog | None = None,
    ) -> None:
        super().__init__()
        self.runner = runner if runner is not None else MakeRunner()
        self.settings = settings if settings is not None else Settings()
        self.catalog = catalog if catalog is not None else Catalog(config_root())
        colors = palette(self.catalog.config.parent)
        theme = TextualTheme(
            name="mocha-green",
            primary=colors["green"],
            secondary=colors["teal"],
            warning=colors["yellow"],
            error=colors["red"],
            accent=colors["green"],
            foreground=colors["text"],
            background=colors["base"],
            surface=colors["surface0"],
            panel=colors["mantle"],
        )
        self.register_theme(theme)
        self.theme = theme.name
        self.suggestions = Suggestions(self.catalog.config, self.settings.home)
        self.loaded: dict[Section, Loaded] = {}
        self.pending: PlanSnapshot | None = None
        self.cancel_event: threading.Event | None = None
        self.quit_when_finished = False

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="base", id="sections"):
            with TabPane("Base system", id="base"):
                yield BaseForm(self.suggestions)
            with TabPane("Working environment", id="workspace"):
                yield WorkspaceForm(self.catalog, self.suggestions)
        yield Static("Ready", id="feedback", markup=False)
        with Horizontal(classes="buttons"):
            yield Button("Inspect", id="inspect")
            yield Button("Plan", id="plan")
            yield Button("Apply plan", id="apply", disabled=True)
            yield Button("Save", id="save")
            yield Button("Reload", id="reload")
            yield Button("Cancel", id="cancel", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        for section in ("base", "workspace"):
            self.reload(section)

    def action_base(self) -> None:
        self.query_one("#sections", TabbedContent).active = "base"

    def action_workspace(self) -> None:
        self.query_one("#sections", TabbedContent).active = "workspace"

    @property
    def section(self) -> Section:
        return (
            "base" if self.query_one("#sections", TabbedContent).active == "base" else "workspace"
        )

    def report(self, message: str) -> None:
        self.query_one("#feedback", Static).update(message)

    def reload(self, section: Section) -> None:
        self.loaded.pop(section, None)
        try:
            saved = self.settings.load(section)
            if isinstance(saved.options, BaseOptions):
                self.query_one(BaseForm).populate(saved.options)
            else:
                self.query_one(WorkspaceForm).populate(saved.options)
            self.loaded[section] = saved
        except ERRORS as exc:
            self.report(f"{section.capitalize()} settings: {exc}")

    def options(self, section: Section) -> BaseOptions | WorkspaceOptions:
        if section not in self.loaded:
            raise ValueError(f"Review the {section} settings and reload")
        return (
            self.query_one(BaseForm).options()
            if section == "base"
            else self.query_one(WorkspaceForm).options()
        )

    def verify_revision(self, section: Section, revision: str | None) -> None:
        if revision is not None and self.settings.load(section).revision != revision:
            raise ValueError("Settings changed since inspection. Reload and review a new plan.")

    def buttons(self) -> None:
        running = self.cancel_event is not None
        for name in RUN_BUTTONS:
            self.query_one("#" + name, Button).disabled = running
        self.query_one("#apply", Button).disabled = running or self.pending is None
        self.query_one("#cancel", Button).disabled = not running and self.pending is None

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        name = event.button.id
        section = self.section
        try:
            if name in ("set-layout", "forget-layout"):
                self.query_one(WorkspaceForm).update_layout(name == "forget-layout")
            elif name == "save":
                saved = self.settings.save(
                    section, self.options(section), self.loaded[section].revision
                )
                self.loaded[section] = saved
                self.report(f"Saved {section} intentions")
            elif name == "reload":
                self.reload(section)
                if section in self.loaded:
                    self.report(f"Reloaded {section} intentions")
            elif name == "apply" and self.pending and self.cancel_event is None:
                self.verify_revision(
                    "base" if self.pending.task == "base" else "workspace", self.pending.revision
                )
                self.push_screen(Consent(self.pending), self.confirmed)
            elif name == "cancel":
                self.cancel()
            elif name in RUN_BUTTONS and self.cancel_event is None:
                self.request(name, section)
        except ERRORS as exc:
            self.report(str(exc))

    def request(self, name: str, section: Section) -> None:
        if name == "inspect":
            self.start(f"{section}-inspect", {}, section, None)
            return
        if name in GROUPS:
            self.start("project-list", {}, section, None, group=GROUPS[name])
            return
        if name in {"shell-configure-plan", "rtk-install-plan"}:
            self.tool_request(ToolChoice(Target(name), {}))
            return
        if name == "display-preview":
            form = self.query_one(WorkspaceForm)
            if not form.monitors:
                raise ValueError("Set an advertised display mode first")
            values = [item.model_dump(mode="json") for item in form.monitors.values()]
            self.start(name, {"monitors": values}, section, None)
            return
        if name == "project-check":
            project = self.query_one(WorkspaceForm).project_path()
            if not project:
                raise ValueError("Enter an existing project directory")
            self.start("project-check", {"project": project}, section, None)
            return
        if name == "restore-plan":
            backup = self.query_one("#backup", Input).value
            if not backup:
                raise ValueError("Choose a recovery backup")
            self.pending = None
            self.start("restore-plan", {"backup_id": backup}, section, ("restore", None))
            return
        revision = self.loaded[section].revision if section in self.loaded else None
        if revision is None:
            raise ValueError(f"Review the {section} settings and reload")
        self.verify_revision(section, revision)
        task: PlanTask = section
        if name == "cleanup-plan":
            task = "cleanup"
            settings = {
                "cleanup": self.query_one("#cleanup", SelectionList).selected,
                "cleanup_mode": self.query_one("#cleanup-mode", Select).value,
            }
        else:
            settings = self.options(section).model_dump(mode="json", exclude_none=True)
            if section == "workspace":
                settings.pop("cleanup", None)
                settings.pop("cleanup_mode", None)
        self.pending = None
        self.start(f"{task}-plan", settings, section, (task, revision))

    def tool_request(self, choice: ToolChoice | None) -> None:
        if choice is None or self.cancel_event:
            return
        task = PROJECT_PLANS.get(choice.target)
        if task is not None:
            self.pending = None
        self.start(choice.target.value, choice.values, "workspace", (task, None) if task else None)

    def confirmed(self, answer: bool | None) -> None:
        if not answer or self.pending is None:
            self.report("Apply cancelled")
            return
        try:
            self.verify_revision(
                "base" if self.pending.task == "base" else "workspace", self.pending.revision
            )
        except ERRORS as exc:
            self.pending = None
            self.report(str(exc))
            self.buttons()
            return
        if self.pending.needs_password:
            self.push_screen(Password(), self.authorized)
        else:
            self.launch_apply(None)

    def authorized(self, password: BecomePassword | None) -> None:
        if not password:
            self.report("Authorization cancelled")
            return
        self.launch_apply(password)

    def launch_apply(self, password: BecomePassword | None) -> None:
        assert self.pending is not None
        active = self.pending
        section: Section = "base" if active.task == "base" else "workspace"
        try:
            self.verify_revision(section, active.revision)
            self.pending = None
            target = (
                active.task + "-user-apply"
                if active.task in {"workspace", "base"} and not active.needs_password
                else active.task + "-apply"
            )
            self.start(
                target,
                json.loads(active.settings_json),
                section,
                None,
                expected=json.loads(active.expected_json),
                password=password,
            )
        except ERRORS as exc:
            self.report(str(exc))

    def start(
        self,
        target: str,
        settings: dict[str, object],
        section: Section,
        planning: tuple[PlanTask, str | None] | None,
        *,
        group: Group | None = None,
        expected: dict[str, object] | None = None,
        password: BecomePassword | None = None,
    ) -> None:
        cancel = threading.Event()
        self.cancel_event = cancel
        self.buttons()
        self.report(f"Running {target}")
        payload = {"schema": 1, "settings": settings, "expected": expected}

        async def perform() -> None:
            nonlocal password
            try:
                goal = Target(target)
                with self.suspend() if goal in FOREGROUND else nullcontext():
                    reply = await asyncio.to_thread(
                        self.runner.run, goal, payload, password=password, cancel=cancel
                    )
                result = TaskResult.model_validate(reply)
                if group and result.status == "ready" and self.section == "workspace":

                    def entries(key: str) -> dict[str, str]:
                        value = result.observed.get(key)
                        if not isinstance(value, dict):
                            return {}
                        return {k: v for k, v in value.items() if isinstance(v, str)}

                    tools = ProjectTools(
                        group, entries("projects"), entries("templates"), entries("git_profiles")
                    )
                    self.push_screen(tools, self.tool_request)
                if target in {"base-inspect", "workspace-inspect"}:
                    self.suggestions.refresh(section, result.observed)
                    self.query_one(WorkspaceForm).refresh_modes()
                if (
                    planning
                    and not cancel.is_set()
                    and result.status in {"ready", "plan_ready"}
                    and result.actions
                    and not result.blockers
                ):
                    self.pending = PlanSnapshot.capture(planning[0], settings, reply, planning[1])
                details = result.describe()
                if target.endswith("-inspect") or target == "project-check":
                    facts = [
                        f"{key.replace('_', ' ')}: {value}"
                        for key, value in result.observed.items()
                        if isinstance(value, (str, bool, int))
                    ]
                    details = "\n".join((details, *facts))
                if target == "template-versions":
                    versions = result.observed.get("versions")
                    if isinstance(versions, list):
                        details += "\n" + ", ".join(str(v) for v in versions)
                self.report(details)
            except ERRORS as exc:
                self.report(str(exc))
            finally:
                if password is not None:
                    password.discard()
                password = None
                self.cancel_event = None
                self.buttons()
                if self.quit_when_finished:
                    self.exit()

        self.run_worker(perform(), name=target)

    def cancel(self) -> None:
        if self.cancel_event:
            self.cancel_event.set()
            self.report("Cancellation requested. Waiting for the current safe boundary.")
        else:
            self.pending = None
            self.report("Plan cancelled")
            self.buttons()

    async def action_quit(self) -> None:
        if self.cancel_event:
            self.quit_when_finished = True
            self.cancel()
        else:
            self.exit()


def main() -> int:
    if os.getuid() == 0 or os.geteuid() != os.getuid():
        print("Open setupSystem as the normal desktop user.", file=sys.stderr)
        return 2
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("setupSystem requires an interactive terminal.", file=sys.stderr)
        return 2
    try:
        SetupApp().run()
    except ERRORS as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
