"""Canonical project discovery and immutable Copier template operations."""

from __future__ import annotations

import asyncio
import hashlib
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field, JsonValue

from setup_plugin_projects import copier_templates, project_layout, sessions, templates
from setup_plugin_projects.template_models import TemplatePlan
from setup_system.infrastructure.user_storage import inside_home
from setup_system_sdk import Inputs, Outcome, Plan, PluginSettings, Resource, Result
from setup_system_sdk.native import NativeBackend


class PluginAction(StrEnum):
    INSPECT_PROJECTS = "inspect-projects"
    OPEN_CODEX = "open-codex"
    OPEN_EDITOR = "open-editor"
    PREPARE_DIRECTORIES = "prepare-directories"
    TEMPLATE_CACHE = "template-cache"
    TEMPLATE_CREATE = "template-create"
    TEMPLATE_UPDATE = "template-update"
    TEMPLATE_VERSIONS = "template-versions"


FOLDER = Path(__file__).parent


class Settings(PluginSettings):
    offline: bool = Field(default=False, title="Use cached template releases")


class Project(Inputs):
    project: str = Field(
        title="Active project",
        max_length=128,
        pattern=r"^[a-z][A-Za-z0-9]*/(?:[A-Za-z][A-Za-z0-9]*|\.github)$",
        json_schema_extra={"ui": {"widget": "select", "choice_provider": "projects"}},
    )


class CatalogTemplate(Inputs):
    template: str = Field(
        pattern=r"^[a-z][a-z0-9]+$",
        max_length=32,
        title="Catalog template",
        json_schema_extra={"ui": {"widget": "select", "choice_provider": "templates"}},
    )


class Template(CatalogTemplate):
    version: str = Field(
        default="",
        max_length=80,
        pattern=r"^[a-zA-Z0-9.+-]*$",
        title="Release",
        description="Leave empty to resolve the latest stable annotated release.",
    )


class Destination(Template):
    destination: Path = Field(
        title="Project directory", description="A directory inside your HOME."
    )


class Creation(Destination):
    project_name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,79}$", title="Project name")
    python_profile: Literal["library", "native", "service"] | None = Field(
        default=None, title="Python template profile"
    )


def template_plan(action: str, selected: Template, settings: Settings) -> TemplatePlan:
    cache = inside_home(templates.template_cache())
    sources = templates.load_templates(FOLDER)
    if selected.template not in sources:
        raise ValueError("Unknown catalog template.")
    destination = (
        inside_home(selected.destination) if isinstance(selected, Destination) else Path.home()
    )
    selected_source = copier_templates.selected_source(
        sources[selected.template],
        cache,
        selected.version or None,
        destination,
        update=action == PluginAction.TEMPLATE_UPDATE,
        offline=settings.offline,
    )
    args: dict[str, JsonValue] = {}
    if isinstance(selected, Creation):
        args["service_name" if selected.template == "service" else "projectName"] = (
            selected.project_name
        )
        if selected.python_profile:
            if selected.template != "python":
                raise ValueError("Python profile belongs to pythonTemplate.")
            args["profile"] = selected.python_profile
    plan = templates.plan_template(
        {selected.template: selected_source},
        template=selected.template,
        version=selected_source.metadata.recommended_version,
        destination=destination,
        operation="update" if action == PluginAction.TEMPLATE_UPDATE else "create",
        offline=settings.offline,
        defaults=True,
        data=args,
    )
    if action != PluginAction.TEMPLATE_CACHE:
        copier_templates.validate_destination(plan)
        copier_templates.copier_executable()
    return plan


def prepare_template(action: str, t: TemplatePlan) -> Plan:
    identity = hashlib.sha256(t.source.model_dump_json().encode()).hexdigest()
    cache_dir = t.cache_root / t.source.metadata.alias / identity
    cached = copier_templates.cached(t)
    rows = [
        Resource(
            kind="template-cache",
            identity=str(cache_dir),
            before=identity if cached else None,
            after=identity,
            ownership="preexisting" if cached else "created",
        )
    ]
    unchanged = action == PluginAction.TEMPLATE_CACHE and cached
    if action != PluginAction.TEMPLATE_CACHE:
        if action == PluginAction.TEMPLATE_UPDATE:
            remembered = copier_templates._read_answers(t.destination).get("_commit")
            unchanged = remembered in (t.version.tag, t.version.commit)
        before = (
            copier_templates._git(t.destination, "rev-parse", "HEAD")
            if action == PluginAction.TEMPLATE_UPDATE
            else None
        )
        rows.append(
            Resource(
                kind="project",
                identity=str(t.destination),
                before=before,
                after=t.version.commit,
                ownership="modified" if before else "created",
            )
        )
    return Plan(
        summary=f"{action} {t.source.project} {t.version.tag} ({t.version.commit})",
        resources=rows,
        outcome=Outcome.NO_CHANGES if unchanged else Outcome.READY,
        warnings=["Cleanup preserves this repository and its project files."],
    )


class Plugin(NativeBackend):
    executable = "/usr/bin/git"
    settings_model = Settings
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        PluginAction.INSPECT_PROJECTS: Inputs,
        PluginAction.OPEN_EDITOR: Project,
        PluginAction.OPEN_CODEX: Project,
        PluginAction.TEMPLATE_VERSIONS: CatalogTemplate,
        PluginAction.PREPARE_DIRECTORIES: Inputs,
        PluginAction.TEMPLATE_CACHE: Template,
        PluginAction.TEMPLATE_CREATE: Creation,
        PluginAction.TEMPLATE_UPDATE: Destination,
    }

    async def choices(self, provider: str, query: str, settings: PluginSettings) -> list[str]:
        if provider == "projects":
            active = await asyncio.to_thread(sessions.paths)
            return [k for k in sorted(active) if query.casefold() in k.casefold()][:128]
        if provider != "templates":
            return []
        sources = await asyncio.to_thread(templates.load_templates, FOLDER)
        return [k for k in sorted(sources) if query.casefold() in k.casefold()]

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id in sessions.PROGRAMS and isinstance(inputs, Project):
            return sessions.session_plan(action_id, inputs.project)
        if (
            action_id == PluginAction.TEMPLATE_VERSIONS
            and isinstance(inputs, CatalogTemplate)
            and isinstance(settings, Settings)
        ):
            source = templates.load_templates(FOLDER).get(inputs.template)
            if not source:
                raise ValueError("Choose a reviewed template.")
            versions = copier_templates.available_versions(
                source, inside_home(templates.template_cache()), offline=settings.offline
            )
            return Plan(
                outcome=Outcome.NO_CHANGES,
                summary="\n".join(r.tag + " " + r.commit for r in versions[:64])
                or "No annotated releases available.",
            )
        if action_id == PluginAction.INSPECT_PROJECTS:
            layout = project_layout.load_layout(FOLDER)
            active = project_layout.active_projects(layout)
            return Plan(
                outcome=Outcome.NO_CHANGES,
                summary="\n".join(
                    f"{project.id} — {'available' if project.id in active else 'absent'}"
                    for project in layout.projects
                ),
            )
        if action_id == PluginAction.PREPARE_DIRECTORIES:
            layout = project_layout.load_layout(FOLDER)
            dirs = project_layout.owner_directories(layout)
            return Plan(
                outcome=Outcome.NO_CHANGES if all(p.is_dir() for p in dirs) else Outcome.READY,
                summary="Prepare reviewed owner directories under Projects.",
                resources=[
                    Resource(
                        kind="directory",
                        identity=str(p),
                        ownership="preexisting" if p.is_dir() else "created",
                    )
                    for p in dirs
                ],
            )
        if not isinstance(inputs, Template) or not isinstance(settings, Settings):
            raise ValueError("Choose a catalog template.")
        try:
            return prepare_template(action_id, template_plan(action_id, inputs, settings))
        except copier_templates.CopierUnavailableError as error:
            return Plan(outcome=Outcome.NEEDS_USER_ACTION, summary=str(error))

    def mutate(
        self, action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan
    ) -> Result:
        if action_id in sessions.PROGRAMS and isinstance(inputs, Project):
            if sessions.session_plan(action_id, inputs.project) != plan:
                return Result(
                    outcome=Outcome.BLOCKED, message="Project session changed. Preview again."
                )
            sessions.start(plan)
        elif action_id == PluginAction.PREPARE_DIRECTORIES:
            project_layout.prepare_owner_directories(project_layout.load_layout(FOLDER))
        elif isinstance(inputs, Template) and isinstance(settings, Settings):
            native = template_plan(action_id, inputs, settings)
            if prepare_template(action_id, native) != plan:
                raise ValueError("Template or project changed since confirmation.")
            if action_id == PluginAction.TEMPLATE_CACHE:
                copier_templates.prepare_cache(native)
            else:
                result = copier_templates.execute_template(native)
                if result.status == "conflicts":
                    return Result(
                        outcome=Outcome.PARTIAL,
                        message="Resolve Copier conflicts in the preserved project tree.",
                    )
        else:
            raise ValueError("Unsupported project action.")
        return Result(
            outcome=Outcome.CHANGED,
            message="Native operation completed; verify reviewed resources.",
            resources=plan.resources,
        )

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        def verify_state() -> bool:
            if plan.resources and plan.resources[0].kind == "user-session":
                return sessions.active(plan.resources[0].identity)
            for row in plan.resources:
                path = inside_home(Path(row.identity))
                if row.kind == "directory" and not path.is_dir():
                    return False
                if row.kind == "template-cache":
                    source = path / "catalog.json"
                    if (
                        not source.is_file()
                        or hashlib.sha256(source.read_bytes()).hexdigest() != row.after
                    ):
                        return False
                if row.kind == "project":
                    answers = copier_templates._read_answers(path)
                    if not isinstance(inputs, Template):
                        return False
                    template_source = templates.load_templates(FOLDER)[inputs.template]
                    if answers.get("_src_path") != template_source.url:
                        return False
                    remembered = answers.get("_commit")
                    if not isinstance(remembered, str) or not row.after:
                        return False
                    cache = Path(plan.resources[0].identity) / "source.git"
                    if (
                        copier_templates._git(
                            cache, "rev-parse", "--verify", remembered + "^{commit}"
                        )
                        != row.after
                    ):
                        return False
            return bool(plan.resources)

        good = await asyncio.to_thread(verify_state)
        return Result(
            outcome=Outcome.COMPLETE if good else Outcome.PARTIAL,
            message="Project resources verified."
            if good
            else "Inspect project files and Copier answers.",
            resources=plan.resources,
        )
