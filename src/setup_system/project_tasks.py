"""The finite project, template, author identity and shell Make tasks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from packaging.version import Version
from pydantic import JsonValue

from setup_system.application import templates
from setup_system.diagnostics import PartialApplyError
from setup_system.domain.actions import ProjectAction
from setup_system.domain.observations import ProjectObservations
from setup_system.domain.receipts import FileState
from setup_system.domain.templates import TemplateSource
from setup_system.infrastructure import copier_templates, git_profiles, project_layout, rtk, shell
from setup_system.infrastructure.file_publication import observe
from setup_system.infrastructure.operation_io import read_wire
from setup_system.infrastructure.user_storage import inside_home
from setup_system.models import (
    CreateProfile,
    CreateTemplate,
    Empty,
    Profile,
    ProjectChoice,
    Repository,
    RequestEnvelope,
    Status,
    TaskResult,
    TemplateChoice,
    TemplateDestination,
    validate_project_settings,
)
from setup_system.runner import resource_root


@dataclass
class Preview:
    result: TaskResult
    apply: Callable[[], object]


def _planned(actions: list[ProjectAction], observed: dict[str, object]) -> TaskResult:
    return TaskResult.model_validate(
        {
            "status": "ready" if actions else "no_changes",
            "actions": tuple(actions),
            "observed": observed,
        }
    )


def paths(root: Path) -> dict[str, Path]:
    return project_layout.active_projects(project_layout.load_layout(root))


def launch(root: Path, selection: ProjectChoice, program: str) -> None:
    available = paths(root)
    if selection.project_id not in available:
        raise ValueError("Choose an exact active catalog project with canonical origins")
    if program not in {"nvim", "codex"}:
        raise ValueError("Unknown project program")
    local = Path.home() / ".local/bin" / program
    binary = str(local) if local.is_file() and os.access(local, os.X_OK) else shutil.which(program)
    if not binary:
        raise ValueError(f"Install {program} in Working environment first")
    argv = [binary, "."] if program == "nvim" else [binary]
    subprocess.run(argv, cwd=available[selection.project_id], check=True)


def _source(root: Path, selected: TemplateChoice) -> tuple[TemplateSource, Path]:
    source = templates.load_templates(root).get(selected.template)
    if source is None:
        raise ValueError("Choose an exact template alias from this catalog")
    cache = inside_home(
        Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "setup-system/templates"
    )
    return source, cache


def _files(paths: list[Path]) -> dict[Path, FileState | None]:
    return {path: observe(inside_home(path)) for path in paths}


def _before(states: dict[Path, FileState | None]) -> dict[str, object]:
    return {str(path): state.model_dump() if state else None for path, state in states.items()}


def _template(root: Path, operation: str, fields: object) -> Preview:
    selected = (
        TemplateChoice.model_validate(fields)
        if operation == "template-cache"
        else CreateTemplate.model_validate(fields)
        if operation == "template-create"
        else TemplateDestination.model_validate(fields)
    )
    source, cache = _source(root, selected)
    destination = (
        inside_home(selected.destination.expanduser())
        if isinstance(selected, TemplateDestination)
        else Path.home()
    )
    source = copier_templates.selected_source(
        source,
        cache,
        selected.version,
        destination,
        update=operation == "template-update",
        offline=selected.offline,
    )
    answers: dict[str, JsonValue] = {}
    if isinstance(selected, CreateTemplate) and selected.project_name:
        answers["service_name" if selected.template == "service" else "projectName"] = (
            selected.project_name
        )
    if isinstance(selected, CreateTemplate) and selected.python_profile:
        answers["profile"] = selected.python_profile
    plan = templates.plan_template(
        {selected.template: source},
        template=selected.template,
        version=source.metadata.recommended_version,
        destination=destination,
        operation="update" if operation == "template-update" else "create",
        data=answers,
        defaults=True,
        offline=selected.offline,
        cache_root=cache.parents[1],
    )
    observed: dict[str, object] = {
        "version": plan.version.version,
        "tag": plan.version.tag,
        "source_commit": plan.version.commit,
        "source": source.url,
        "destination": str(destination),
        "source_versions": {v.tag: v.commit for v in source.metadata.versions},
    }
    if operation == "template-cache":
        actions = (
            []
            if copier_templates.cached(plan)
            else [f"Cache {source.project} at {plan.version.tag}"]
        )

        def run() -> object:
            return copier_templates.prepare_cache(plan)
    else:
        copier_templates.copier_executable()
        copier_templates.validate_destination(plan)
        if plan.operation == "update":
            observed["before_head"] = subprocess.check_output(
                ["git", "-C", str(destination), "rev-parse", "HEAD"], text=True
            ).strip()
        actions = [
            f"Copier {plan.operation} {destination} from {source.project} "
            f"{plan.version.tag} ({plan.version.commit})"
        ]

        def run() -> object:
            result = copier_templates.execute_template(plan)
            if result.status == "conflicts":
                raise PartialApplyError(
                    "Copier produced conflicts; inspect the preserved project working tree"
                )
            return result

    return Preview(
        _planned(
            [
                ProjectAction.model_validate(
                    {
                        "kind": operation,
                        "target": str(cache if operation == "template-cache" else destination),
                        "label": label,
                    }
                )
                for label in actions
            ],
            observed,
        ),
        run,
    )


def _git(operation: str, fields: object) -> Preview:
    choice = (
        CreateProfile.model_validate(fields)
        if operation == "git-configure"
        else Profile.model_validate(fields)
    )
    repo = git_profiles.exact_repository(choice.repository)
    profile = git_profiles.profile_path(choice.name)
    before = _files([profile, repo / ".git/config"])
    observed: dict[str, object] = {}
    if isinstance(choice, CreateProfile):
        directories = [choice.owner_directory] if choice.owner_directory else []
        global_path = Path(os.environ.get("GIT_CONFIG_GLOBAL", Path.home() / ".gitconfig"))
        before.update(_files([global_path]))
        identity = git_profiles.identity_digest(repo)
        observed["identity"] = identity
        _, changes = git_profiles.configure_profile(choice.name, repo, directories)

        def write() -> object:
            return git_profiles.configure_profile(
                choice.name,
                repo,
                directories,
                yes=True,
                reviewed=before,
                expected_identity=identity,
            )
    else:
        _, changes = git_profiles.use_profile(choice.name, repo)

        def write() -> object:
            return git_profiles.use_profile(choice.name, repo, yes=True, reviewed=before)

    observed["before"] = _before(before)
    actions = [
        ProjectAction.model_validate(
            {
                "kind": operation,
                "target": str(path),
                "fields": keys,
                "label": f"Update {path}: {', '.join(keys)}",
            }
        )
        for path, keys in changes.items()
    ]
    return Preview(_planned(actions, observed), write)


def preview(
    root: Path, operation: str, fields: object, accepted: TaskResult | None = None
) -> Preview:
    if operation in {"template-cache", "template-create", "template-update"}:
        if accepted and isinstance(fields, dict) and not fields.get("version"):
            fields = {**fields, "version": accepted.observed.get("version")}
        return _template(root, operation, fields)
    if operation in {"git-configure", "git-use"}:
        return _git(operation, fields)
    Empty.model_validate(fields)
    if operation == "project-prepare":
        layout = project_layout.load_layout(root)
        dirs = project_layout.owner_directories(layout)
        changes = tuple(
            ProjectAction(kind="project-prepare", target=str(path), label=f"Create {path}")
            for path in dirs
            if not path.exists()
        )
        return Preview(
            TaskResult(status="ready" if changes else "no_changes", actions=changes),
            lambda: project_layout.prepare_owner_directories(layout),
        )
    if operation == "shell-configure":
        status, updates = shell.plan_shell(root)
        if not status.zsh:
            raise ValueError("Install Zsh before configuring its startup files")
        shell_changes = [
            ProjectAction(kind="shell-configure", target=str(p), label=f"Configure {p}")
            for p, state in updates
            if state != "unchanged"
        ]
        before = _files([p for p, _ in updates])
        return Preview(
            _planned(shell_changes, {"before": _before(before)}),
            lambda: shell.apply_shell(root, reviewed=before),
        )
    if operation == "rtk-install":
        version = accepted.observed.get("version") if accepted else None
        selected = rtk.release(str(version) if version else None)
        binary, text = rtk.plan_rtk(selected)
        if binary.path and not binary.product_matches:
            raise ValueError(text)
        target = binary.path or Path.home() / ".local/bin/rtk"
        changes = (
            ()
            if binary.version == selected.version
            else (ProjectAction(kind="rtk-install", target=str(target), label=text),)
        )
        prior = _files([target]) if target.is_relative_to(Path.home()) else {}
        observed = {
            **selected.model_dump(),
            "target": str(target),
            "before": _before(prior),
            "install_mode": 0o755,
        }
        if changes and not target.is_relative_to(Path.home()):
            raise ValueError("Existing RTK is managed by its system package")
        return Preview(
            TaskResult(
                status="ready" if changes else "no_changes",
                actions=changes,
                observed=ProjectObservations.model_validate(observed),
            ),
            lambda: rtk.install_rtk(selected, update=True, reviewed=prior),
        )
    raise ValueError("Unknown project operation")


def execute(root: Path, target: str, payload: dict[str, object]) -> TaskResult:
    if os.geteuid() == 0:
        raise ValueError("Project operations require the normal desktop user")
    envelope = RequestEnvelope.model_validate(payload).for_target(target)
    operation = target.removesuffix("-apply").removesuffix("-plan")
    fields = validate_project_settings(operation, envelope.settings)
    if target == "project-list":
        Empty.model_validate(fields)
        return TaskResult(
            status="ready",
            observed=ProjectObservations.model_validate(
                {
                    "projects": {k: str(v) for k, v in paths(root).items()},
                    "templates": {k: v.url for k, v in templates.load_templates(root).items()},
                    "git_profiles": {k: str(v) for k, v in git_profiles.list_profiles()},
                }
            ),
        )
    if target == "template-versions":
        choice = TemplateChoice.model_validate(fields)
        source, cache = _source(root, choice)
        rows = copier_templates.available_versions(source, cache, offline=choice.offline)
        recommended = next(
            (r.version for r in rows if not Version(r.version).is_prerelease),
            rows[0].version if rows else None,
        )
        return TaskResult(
            status="ready",
            observed=ProjectObservations(
                versions=tuple(r.version for r in rows), recommended=recommended
            ),
        )
    if target == "git-inspect":
        repo = Repository.model_validate(fields).repository
        return TaskResult(
            status="ready",
            observed=ProjectObservations(identity=dict(git_profiles.inspect_repository(repo))),
        )
    if target in {"project-editor", "project-codex"}:
        launch(
            root,
            ProjectChoice.model_validate(fields),
            "nvim" if target == "project-editor" else "codex",
        )
        return TaskResult(status="complete")
    operation, _, phase = target.rpartition("-")
    if phase not in ("plan", "apply"):
        raise ValueError("Unknown project operation")
    accepted = envelope.expected
    proposed = preview(root, operation, fields, accepted)
    if phase == "plan":
        return proposed.result
    if (
        not accepted
        or not accepted.same_plan(proposed.result)
        or accepted.status != "ready"
        or not accepted.actions
    ):
        raise ValueError("A matching confirmation is required; refresh the project plan")
    proposed.apply()
    return TaskResult(status="complete", actions=accepted.actions, observed=accepted.observed)


def main() -> None:
    (target,) = sys.argv[1:]
    request = Path(os.environ["SETUP_SYSTEM_REQUEST"])
    result = Path(os.environ["SETUP_SYSTEM_RESULT"])
    try:
        reply = execute(resource_root(), target, json.loads(read_wire(request)))
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        status: Status = (
            "partial"
            if isinstance(error, PartialApplyError)
            else "failed"
            if target.endswith("-apply")
            else "blocked"
        )
        reply = TaskResult(status=status, blockers=(str(error),))
    result.write_text(reply.model_dump_json(exclude_unset=True))


if __name__ == "__main__":
    main()
