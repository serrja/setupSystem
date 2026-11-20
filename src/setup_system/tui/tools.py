"""Subject-specific project, template, shell, Git and recovery forms."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, cast

from pydantic import JsonValue
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, TabbedContent, TabPane

from setup_system.application import templates
from setup_system.application.tools import ToolAction, ToolRequest, projects
from setup_system.infrastructure.file_receipts import list_operations


class ToolsScreen(ModalScreen[ToolRequest | None]):
    DEFAULT_CSS = """
    ToolsScreen { align: center middle; }
    ToolsScreen > VerticalScroll { width: 95%; height: 95%; padding: 0 1; border: round $accent; }
    ToolsScreen TabbedContent { height: auto; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Close")]

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.sources = templates.load_templates(root)
        self.projects = projects(root)
        self.history = list_operations()

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
        menu.set_options(
            [(v.version, v.version) for v in template.metadata.versions] if template else []
        )
        if template and template.metadata.recommended_version:
            menu.value = template.metadata.recommended_version

    @on(Button.Pressed)
    def submit(self, event: Button.Pressed) -> None:
        action = cast(ToolAction, (event.button.id or "").replace("-", "_"))

        def chosen(widget: str) -> str:
            value = self.query_one("#" + widget, Select).value
            return "" if value is Select.BLANK else str(value)

        def entry(widget: str) -> str:
            return self.query_one("#" + widget, Input).value.strip()

        request = ToolRequest(action)
        if action in {"open_project", "open_codex"}:
            request = ToolRequest(action, name=chosen("project"))
        elif action in {"create_template", "update_template", "template_cache"}:
            name, destination, project = (
                chosen("template"),
                entry("template-directory"),
                entry("project-name"),
            )
            if not name or (action != "template_cache" and not destination):
                self.notify("Select a template and enter the project directory.")
                return
            if action == "create_template" and not project:
                self.notify("Enter a project name to create this template.")
                return
            answers: dict[str, JsonValue] = {}
            if project:
                answers["service_name" if name == "service" else "projectName"] = project
            if action == "create_template" and name == "python":
                answers["profile"] = chosen("python-profile")
            request = ToolRequest(
                action,
                name=name,
                destination=Path(destination).expanduser() if destination else None,
                version=chosen("template-version") or None,
                answers=answers,
            )
        elif action in {"git_use", "git_configure", "git_inspect"}:
            directory = entry("git-directory")
            if not directory:
                self.notify("Enter the exact repository directory.")
                return
            owner = entry("owner-directory")
            request = ToolRequest(
                action,
                name=entry("git-profile"),
                destination=Path(directory).expanduser(),
                owner_directory=Path(owner).expanduser() if owner else None,
            )
        elif action in {"restore", "restore_preview"}:
            if not chosen("recovery-operation"):
                self.notify("Select a recorded operation.")
                return
            request = ToolRequest(action, backup_id=chosen("recovery-operation"))
        self.dismiss(request)

    def action_cancel(self) -> None:
        self.dismiss(None)
