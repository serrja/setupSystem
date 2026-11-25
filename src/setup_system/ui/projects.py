"""Project, template and Git profile forms for native workspace tasks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal

from pydantic import JsonValue
from textual import on
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select

from setup_system.runner import Target


@dataclass(frozen=True)
class ToolChoice:
    target: Target
    values: dict[str, JsonValue]


class ProjectTools(ModalScreen[ToolChoice | None]):
    """Collect one subject-specific request and return to the permanent workspace."""

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "close", "Back")]

    def __init__(
        self,
        group: Literal["projects", "templates", "git"],
        projects: dict[str, str],
        templates: dict[str, str],
        profiles: dict[str, str],
    ) -> None:
        super().__init__()
        self.group = group
        self.projects = projects
        self.templates = templates
        self.profiles = profiles

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="dialog"):
            if self.group == "projects":
                yield Label("Catalog checkouts")
                yield Select(
                    [(key, key) for key in self.projects], id="checkout", prompt="Choose a checkout"
                )
                yield Button("Open in Neovim", id="project-editor")
                yield Button("Open in Codex", id="project-codex")
                yield Button("Preview project directory preparation", id="project-prepare-plan")
            elif self.group == "templates":
                yield Label("Project template")
                yield Select(
                    [(key, key) for key in self.templates],
                    id="template",
                    prompt="Choose a template",
                )
                yield Input(placeholder="Optional stable version", id="version")
                yield Checkbox("Use verified offline cache", id="offline")
                yield Button("List releases", id="template-versions")
                yield Button("Preview offline cache", id="template-cache-plan")
                yield Input(placeholder="Absolute destination directory", id="destination")
                yield Input(placeholder="Project name for a new checkout", id="project-name")
                yield Select(
                    [(name.title(), name) for name in ("library", "service", "native")],
                    id="python-profile",
                    prompt="Optional Python project profile",
                )
                yield Button("Preview new project", id="template-create-plan")
                yield Button("Preview template update", id="template-update-plan")
            else:
                yield Label("Git repository")
                yield Input(placeholder="Absolute checkout directory", id="repository")
                yield Button("Inspect Git identity", id="git-inspect")
                yield Label("Save identity from this checkout")
                yield Input(placeholder="Profile name", id="profile-name")
                yield Input(placeholder="Optional owner directory", id="owner-directory")
                yield Button("Preview saved profile", id="git-configure-plan")
                yield Label("Use a saved identity")
                yield Select(
                    [(key, key) for key in self.profiles], id="profile", prompt="Choose a profile"
                )
                yield Button("Preview repository identity", id="git-use-plan")
            yield Label("", id="form-error")
            yield Button("Back", id="back")

    @on(Select.Changed, "#template")
    def template_changed(self, event: Select.Changed) -> None:
        self.query_one("#python-profile", Select).display = event.value == "python"

    def on_mount(self) -> None:
        if self.group == "templates":
            self.query_one("#python-profile", Select).display = False

    def action_close(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "back":
            self.action_close()
            return
        if event.button.id is None:
            return
        target = Target(event.button.id)
        values: dict[str, JsonValue] = {}

        def selected(widget: str) -> str:
            item = self.query_one(widget, Select).value
            if not isinstance(item, str):
                raise ValueError("Choose an item from the list")
            return item

        try:
            if target in {Target.PROJECT_EDITOR, Target.PROJECT_CODEX}:
                values["project_id"] = selected("#checkout")
            elif self.group == "templates":
                values["template"] = selected("#template")
                values["offline"] = self.query_one("#offline", Checkbox).value
                if target == Target.TEMPLATE_CREATE_PLAN and values["template"] == "python":
                    profile = self.query_one("#python-profile", Select).value
                    if isinstance(profile, str):
                        values["python_profile"] = profile
                for key in ("version", "destination", "project_name"):
                    field = self.query_one("#" + key.replace("_", "-"), Input).value
                    if field and (
                        key == "version"
                        or target in {Target.TEMPLATE_CREATE_PLAN, Target.TEMPLATE_UPDATE_PLAN}
                    ):
                        if key != "project_name" or target == Target.TEMPLATE_CREATE_PLAN:
                            values[key] = field
            elif self.group == "git":
                values["repository"] = self.query_one("#repository", Input).value
                if target == Target.GIT_USE_PLAN:
                    values["name"] = selected("#profile")
                if target == Target.GIT_CONFIGURE_PLAN:
                    values["name"] = self.query_one("#profile-name", Input).value
                    if owner := self.query_one("#owner-directory", Input).value:
                        values["owner_directory"] = owner
        except ValueError as exc:
            self.query_one("#form-error", Label).update(str(exc))
            return
        self.dismiss(ToolChoice(target, values))
