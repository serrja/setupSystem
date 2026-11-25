"""Normal-user Textual entry point for the native Make tasks."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import nullcontext
from typing import ClassVar, Literal

from pydantic import JsonValue
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

from setup_system.domain.observations import NativeObservations, ProjectObservations
from setup_system.infrastructure.operation_outcome import read_last_outcome
from setup_system.infrastructure.palette import palette
from setup_system.layout import GUIDES, INSTALLED
from setup_system.models import (
    BaseOptions,
    CleanupOptions,
    PlanSnapshot,
    PlanTask,
    ProjectTask,
    Section,
    Status,
    WorkspaceOptions,
)
from setup_system.runner import (
    FOREGROUND,
    AsyncMakeRunner,
    BecomePassword,
    Command,
    RunEvent,
    Target,
)
from setup_system.settings import Loaded, Settings
from setup_system.suggestions import Catalog, Suggestions, config_root
from setup_system.ui.dialogs import Consent, DiscardEdits, Password
from setup_system.ui.forms import BaseForm, WorkspaceForm
from setup_system.ui.help import Help
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
    "admin-plan",
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
        ("f1", "help", "Help"),
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
    Consent, Password, DiscardEdits { align: center middle; }
    #dialog { width: 90%; height: auto; max-height: 90%; padding: 1;
              border: solid $accent; background: $surface; }
    #review { height: auto; max-height: 12; }
    """

    def __init__(
        self,
        runner: AsyncMakeRunner | None = None,
        settings: Settings | None = None,
        catalog: Catalog | None = None,
    ) -> None:
        super().__init__()
        self.runner = runner if runner is not None else AsyncMakeRunner()
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
        self.cancel_event: asyncio.Event | None = None
        self.quit_when_finished = False
        self.saving: set[Section] = set()
        self.reloading: set[Section] = set()
        self.reviewing: PlanSnapshot | None = None
        self.controls: dict[str, Button] = {}
        self.navigation: TabbedContent
        self.base_form: BaseForm
        self.workspace_form: WorkspaceForm
        self.feedback: Static
        self.feedback_updated = False

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
        self.navigation = self.query_one("#sections", TabbedContent)
        self.base_form = self.query_one(BaseForm)
        self.workspace_form = self.query_one(WorkspaceForm)
        self.feedback = self.query_one("#feedback", Static)
        self.controls = {
            name: self.query_one("#" + name, Button) for name in (*RUN_BUTTONS, "apply", "cancel")
        }
        self.run_worker(self.reload("base"), name="load-base")
        self.run_worker(self.reload("workspace"), name="load-workspace")
        self.run_worker(self.last_outcome(), name="last-outcome")

    async def last_outcome(self) -> None:
        try:
            outcome = await read_last_outcome()
            if outcome is None:
                return
            message = outcome.describe()
        except (OSError, ValueError):
            message = (
                "Could not read the previous operation record. Inspect before applying changes."
            )
        if not self.feedback_updated:
            self.report(message)

    def action_help(self) -> None:
        root = self.catalog.config.parent
        directory = GUIDES if root == INSTALLED else root / "docs"
        self.push_screen(Help(directory))

    def action_base(self) -> None:
        self.navigation.active = "base"

    def action_workspace(self) -> None:
        self.navigation.active = "workspace"

    @property
    def section(self) -> Section:
        return "base" if self.navigation.active == "base" else "workspace"

    @property
    def view_active(self) -> bool:
        """Allow worker updates while the application is processing UI messages."""
        return self.is_running and not self.quit_when_finished

    def report(self, message: str) -> None:
        if self.view_active and self.feedback.is_attached:
            self.feedback.update(message)
            self.feedback_updated = True

    async def reload(self, section: Section, *, announce: bool = False) -> None:
        if not self.view_active or section in self.reloading or section in self.saving:
            return
        self.reloading.add(section)
        form = self.base_form if section == "base" else self.workspace_form
        try:
            if announce and section in self.loaded:
                try:
                    dirty = form.options() != self.loaded[section].options
                except ValueError:
                    dirty = True
                if dirty and not await self.push_screen(
                    DiscardEdits(section), wait_for_dismiss=True
                ):
                    return
            self.loaded.pop(section, None)
            form.disabled = True
            saved = await self.settings.read(section)
            if not self.view_active or not form.is_attached:
                return
            if isinstance(saved.options, BaseOptions):
                self.base_form.populate(saved.options)
            else:
                self.workspace_form.populate(saved.options)
            self.loaded[section] = saved
            if announce:
                self.report(f"Reloaded {section} intentions")
        except ERRORS as exc:
            self.report(f"{section.capitalize()} settings: {exc}")
        finally:
            if self.view_active and form.is_attached:
                form.disabled = False
            self.reloading.discard(section)

    async def save_section(
        self, section: Section, options: BaseOptions | WorkspaceOptions, revision: str
    ) -> None:
        try:
            saved = await self.settings.publish(section, options, revision)
            if self.view_active:
                self.loaded[section] = saved
                self.report(f"Saved {section} intentions")
        except ERRORS as exc:
            self.report(str(exc))
        finally:
            self.saving.discard(section)
            if self.quit_when_finished and not self.saving and self.cancel_event is None:
                self.exit()

    def options(self, section: Section) -> BaseOptions | WorkspaceOptions:
        if section not in self.loaded:
            raise ValueError(f"Review the {section} settings and reload")
        return self.base_form.options() if section == "base" else self.workspace_form.options()

    async def verify_revision(self, section: Section, revision: str | None) -> None:
        if revision is not None and (await self.settings.read(section)).revision != revision:
            raise ValueError("Settings changed since inspection. Reload and review a new plan.")

    def buttons(self) -> None:
        if not self.view_active:
            return
        running = self.cancel_event is not None or self.reviewing is not None
        for name in RUN_BUTTONS:
            self.controls[name].disabled = running
        apply = self.controls["apply"]
        apply.label = f"Apply {self.pending.section} plan" if self.pending else "Apply plan"
        apply.disabled = running or self.pending is None
        self.controls["cancel"].disabled = not running and self.pending is None

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        if not self.view_active:
            return
        name = event.button.id
        section = self.section
        try:
            if name in ("set-layout", "forget-layout"):
                self.query_one(WorkspaceForm).update_layout(name == "forget-layout")
            elif name == "save":
                if section not in self.saving:
                    options = self.options(section)
                    revision = self.loaded[section].revision
                    self.saving.add(section)
                    self.run_worker(
                        self.save_section(section, options, revision), name="save-" + section
                    )
            elif name == "reload" and section not in self.saving:
                self.run_worker(self.reload(section, announce=True), name="reload-" + section)
            elif name == "apply" and self.pending and not (self.cancel_event or self.reviewing):
                self.reviewing = self.pending
                self.buttons()
                self.run_worker(self.review_apply(self.pending), name="review-apply")
            elif name == "cancel":
                self.cancel()
            elif name in RUN_BUTTONS and not (self.cancel_event or self.reviewing):
                self.request(name, section)
        except ERRORS as exc:
            self.report(str(exc))

    def request(self, name: str, section: Section) -> None:
        if name == "admin-plan":
            username = self.query_one("#admin-account", Input).value.strip()
            self.pending = None
            self.start(name, {"username": username}, "base", ("admin", None))
            return
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
            values: list[JsonValue] = [
                item.model_dump(mode="json") for item in form.monitors.values()
            ]
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
        task: PlanTask = section
        if name == "cleanup-plan":
            task = "cleanup"
            settings = CleanupOptions.model_validate(
                {
                    "cleanup": self.query_one("#cleanup", SelectionList).selected,
                    "cleanup_mode": self.query_one("#cleanup-mode", Select).value,
                }
            ).model_dump(mode="json")
        else:
            settings = self.options(section).model_dump(mode="json", exclude_none=True)
            if section == "workspace":
                settings.pop("cleanup", None)
                settings.pop("cleanup_mode", None)
        self.pending = None
        self.start(f"{task}-plan", settings, section, (task, revision))

    def tool_request(self, choice: ToolChoice | None) -> None:
        if choice is None or self.cancel_event or not self.view_active:
            return
        task = PROJECT_PLANS.get(choice.target)
        if task is not None:
            self.pending = None
        self.start(choice.target.value, choice.values, "workspace", (task, None) if task else None)

    async def review_apply(self, plan: PlanSnapshot) -> None:
        """Keep consent and its one-use credential bound to the same preview."""
        section = plan.section
        password = None

        def current() -> bool:
            return (
                self.view_active
                and self.navigation.screen.is_active
                and self.pending is plan
                and self.reviewing is plan
            )

        try:
            await self.verify_revision(section, plan.revision)
            if not current():
                return
            if not await self.push_screen(Consent(plan), wait_for_dismiss=True):
                self.report("Apply cancelled")
                return
            if not current():
                return
            await self.verify_revision(section, plan.revision)
            if not current():
                return
            if plan.needs_password:
                await self.runner.check_authorization()
                if not current():
                    return
                password = await self.push_screen(
                    Password(root_account=plan.task == "admin"), wait_for_dismiss=True
                )
                if password is None:
                    self.report("Authorization cancelled")
                    return
                if not current():
                    return
                await self.verify_revision(section, plan.revision)
                if not current():
                    return
            target = (
                plan.task + "-user-apply"
                if plan.task in {"workspace", "base"} and not plan.needs_password
                else plan.task + "-apply"
            )
            self.pending = None
            self.start(
                target,
                json.loads(plan.settings_json),
                section,
                None,
                expected=json.loads(plan.expected_json),
                password=password,
            )
            password = None  # The operation worker owns it now.
        except ERRORS as exc:
            if self.pending is plan:
                self.pending = None
            self.report(str(exc))
        finally:
            if password is not None:
                password.discard()
            if self.reviewing is plan:
                self.reviewing = None
                self.buttons()

    def start(
        self,
        target: str,
        settings: dict[str, JsonValue],
        section: Section,
        planning: tuple[PlanTask, str | None] | None,
        *,
        group: Group | None = None,
        expected: dict[str, JsonValue] | None = None,
        password: BecomePassword | None = None,
    ) -> None:
        command = Command.capture(target, {"schema": 1, "settings": settings, "expected": expected})
        cancel = asyncio.Event()
        self.cancel_event = cancel
        self.buttons()
        self.report(f"Running {target}")

        def progress(event: RunEvent) -> None:
            self.report(event.message)

        async def perform() -> None:
            nonlocal password
            try:
                if planning:
                    await self.verify_revision(section, planning[1])
                    if cancel.is_set():
                        self.report("Plan cancelled")
                        return
                if not self.view_active:
                    return
                with self.suspend() if command.target in FOREGROUND else nullcontext():
                    result = await self.runner.execute(
                        command, password=password, cancel=cancel, on_event=progress
                    )
                if not self.view_active:
                    return
                observed = result.observed.model_dump(mode="json", exclude_unset=True)
                if (
                    group
                    and result.status is Status.READY
                    and self.section == "workspace"
                    and self.navigation.screen.is_active
                ):
                    if not isinstance(result.observed, ProjectObservations):
                        raise ValueError("The project operation returned incompatible facts")
                    tools = ProjectTools(
                        group,
                        result.observed.projects,
                        result.observed.templates,
                        result.observed.git_profiles,
                    )
                    self.push_screen(tools, self.tool_request)
                if target in {"base-inspect", "workspace-inspect"}:
                    if not isinstance(result.observed, NativeObservations):
                        raise ValueError("Inspection returned incompatible facts")
                    await self.suggestions.refresh(section, result.observed)
                    if not self.view_active or not self.workspace_form.is_attached:
                        return
                    self.base_form.refresh_choices()
                    self.workspace_form.refresh_choices()
                if (
                    planning
                    and not cancel.is_set()
                    and result.status.allows_confirmation
                    and result.actions
                    and not result.blockers
                ):
                    self.pending = PlanSnapshot.capture(
                        planning[0],
                        json.loads(command.request_json)["settings"],
                        result.model_dump(mode="json", exclude_unset=True),
                        planning[1],
                    )
                details = result.describe()
                if target.endswith("-inspect") or target == "project-check":
                    facts = [
                        f"{key.replace('_', ' ')}: {value}"
                        for key, value in observed.items()
                        if isinstance(value, (str, bool, int))
                    ]
                    details = "\n".join((details, *facts))
                if target == "template-versions" and isinstance(
                    result.observed, ProjectObservations
                ):
                    details += "\n" + ", ".join(result.observed.versions)
                self.report(details)
            except ERRORS as exc:
                self.report(str(exc))
            finally:
                if password is not None:
                    password.discard()
                password = None
                self.cancel_event = None
                if self.quit_when_finished:
                    if not self.saving:
                        self.exit()
                else:
                    self.buttons()

        self.run_worker(perform(), name=target)

    def cancel(self) -> None:
        if self.cancel_event:
            self.cancel_event.set()
            self.report("Cancellation requested. Waiting for the current safe boundary.")
        else:
            self.pending = None
            self.reviewing = None
            self.report("Plan cancelled")
            self.buttons()

    async def action_quit(self) -> None:
        self.quit_when_finished = True
        if self.cancel_event or self.saving:
            if self.cancel_event:
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
