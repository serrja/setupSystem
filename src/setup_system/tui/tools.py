"""Subject-specific project, template, shell, Git and recovery forms."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic import JsonValue
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, TabbedContent, TabPane

from setup_system.domain.tool_requests import (
    CacheTemplate,
    ConfigureGit,
    InspectGit,
    OpenProject,
    RestoreTool,
    SimpleTool,
    ToolChoices,
    ToolRequest,
    UseGitProfile,
    WriteTemplate,
    snapshot_answers,
)


class ToolsScreen(ModalScreen[ToolRequest | None]):
    DEFAULT_CSS = """
    ToolsScreen { align: center middle; }
    ToolsScreen > VerticalScroll { width: 95%; height: 95%; padding: 0 1; border: round $accent; }
    ToolsScreen TabbedContent { height: auto; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Close")]

    def __init__(self, choices: ToolChoices) -> None:
        super().__init__()
        self.sources = {item.name: item for item in choices.templates}
        self.projects = choices.projects
        self.history = choices.history

    def compose(self) -> ComposeResult:
        with VerticalScroll(), TabbedContent():
            with TabPane("Projects"):
                yield Select(
                    [(name, name) for name in self.projects],
                    prompt="Select a local checkout",
                    id="project",
                )
                yield Button("Open in Neovim", id="open-project")
                yield Button("Open Codex in this project", id="open-codex")
                yield Button("Prepare project directories", id="prepare-projects")
            with TabPane("Templates"):
                yield Select(
                    [(key, key) for key in self.sources], prompt="Select a template", id="template"
                )
                yield Select([], prompt="Recommended version", id="template-version")
                yield Input(placeholder="Project directory", id="template-directory")
                yield Input(placeholder="Project name for creation", id="project-name")
                yield Select(
                    [(name.title(), name) for name in ("library", "service", "native")],
                    value="library",
                    allow_blank=False,
                    id="python-profile",
                    disabled=True,
                )
                yield Button("Create project", id="create-template")
                yield Button("Update existing project", id="update-template")
                yield Button("Prepare offline template", id="template-cache")
            with TabPane("Git"):
                yield Input(placeholder="Exact repository directory", id="git-directory")
                yield Input(placeholder="Identity profile name", id="git-profile")
                yield Input(
                    placeholder="Use profile for repositories below this directory (optional)",
                    id="owner-directory",
                )
                yield Button("Inspect identity", id="git-inspect")
                yield Button("List saved profiles", id="git-profiles")
                yield Button("Save repository identity as profile", id="git-configure")
                yield Button("Use saved identity here", id="git-use")
            with TabPane("Shell and fonts"):
                yield Button("Configure Zsh startup files", id="shell")
                yield Button("Install or update RTK", id="rtk")
                yield Button("Inspect fonts", id="font-plan")
                yield Button("Apply font configuration", id="font-apply")
                yield Button("Show glyph sample", id="font-probe")
            with TabPane("Recovery", id="recovery-pane"):
                yield Select(
                    [(f"{key} · {state}", key) for key, state in self.history],
                    prompt="Select a recorded operation",
                    id="recovery-operation",
                )
                if not self.history:
                    yield Label("No recorded user operations.")
                yield Button(
                    "Preview affected files", id="restore-preview", disabled=not self.history
                )
                yield Button("Restore recorded changes", id="restore", disabled=not self.history)

    @on(Select.Changed, "#template")
    def versions(self, event: Select.Changed) -> None:
        template = self.sources.get(str(event.value))
        self.query_one("#python-profile", Select).disabled = str(event.value) != "python"
        menu = self.query_one("#template-version", Select)
        menu.set_options([(version, version) for version in template.versions] if template else [])
        if template and template.recommended:
            menu.value = template.recommended

    @on(Button.Pressed)
    def submit(self, event: Button.Pressed) -> None:
        try:
            request = self.request_for((event.button.id or "").replace("-", "_"))
        except ValueError as exc:
            self.notify(str(exc), severity="warning")
            return
        self.dismiss(request)

    def request_for(self, action: str) -> ToolRequest:
        def chosen(widget: str) -> str:
            value = self.query_one("#" + widget, Select).value
            return "" if value is Select.BLANK else str(value)

        def entry(widget: str) -> str:
            return self.query_one("#" + widget, Input).value.strip()

        if action == "open_project" or action == "open_codex":
            if not chosen("project"):
                raise ValueError("Select a local checkout.")
            return OpenProject.model_validate({"action": action, "name": chosen("project")})
        if action in {"create_template", "update_template", "template_cache"}:
            name, version = chosen("template"), chosen("template-version") or None
            if not name:
                raise ValueError("Select a template.")
            if action == "template_cache":
                return CacheTemplate(name=name, version=version)
            project, destination = entry("project-name"), entry("template-directory")
            if not destination:
                raise ValueError("Enter the project directory.")
            if action == "create_template" and not project:
                raise ValueError("Enter a project name to create this template.")
            answers: dict[str, JsonValue] = {}
            if project:
                answers["service_name" if name == "service" else "projectName"] = project
            if action == "create_template" and name == "python":
                answers["profile"] = chosen("python-profile")
            return WriteTemplate(
                action="create_template" if action == "create_template" else "update_template",
                name=name,
                destination=Path(destination).expanduser(),
                version=version,
                answers=snapshot_answers(answers),
            )
        if action in {"git_use", "git_configure", "git_inspect"}:
            directory = entry("git-directory")
            if not directory:
                raise ValueError("Enter the exact repository directory.")
            repo = Path(directory).expanduser()
            if action == "git_inspect":
                return InspectGit(destination=repo)
            name = entry("git-profile")
            if not name:
                raise ValueError("Enter the identity profile name.")
            if action == "git_use":
                return UseGitProfile(name=name, destination=repo)
            owner = entry("owner-directory")
            return ConfigureGit(
                name=name,
                destination=repo,
                owner_directory=Path(owner).expanduser() if owner else None,
            )
        if action == "restore" or action == "restore_preview":
            if not chosen("recovery-operation"):
                raise ValueError("Select a recorded operation.")
            return RestoreTool.model_validate(
                {"action": action, "backup_id": chosen("recovery-operation")}
            )
        return SimpleTool.model_validate({"action": action})

    def action_cancel(self) -> None:
        self.dismiss(None)
