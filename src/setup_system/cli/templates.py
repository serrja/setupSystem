"""Generic project templates."""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer
from copier.errors import CopierError
from pydantic import JsonValue, TypeAdapter, ValidationError

from setup_system.application.project_layout import (
    active_projects,
    load_layout,
)
from setup_system.application.templates import (
    TemplateSource,
    execute_template,
    load_templates,
    plan_template,
    prepare_cache,
)
from setup_system.cli._context import ROOT

templates_app = typer.Typer(help="Generic project templates.")


@templates_app.command("list")
def templates_list() -> None:
    """Show trusted templates, pinned recommendations and optional local checkouts."""
    try:
        projects = active_projects(load_layout(ROOT))
        sources = load_templates(ROOT)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    for alias, source in sources.items():
        version = source.metadata.recommended_version or "no approved version"
        typer.echo(
            f"{alias}: {source.project} [{version}] — "
            f"{projects.get(source.project, 'not installed')}"
        )


@templates_app.command("path")
def templates_path(
    name: Annotated[str, typer.Option(help="Template repository name or catalog alias.")],
) -> None:
    """Resolve an existing template checkout; generation does not require this path."""
    sources = load_templates(ROOT)
    source = next(
        (item for alias, item in sources.items() if name in {alias, item.project.split("/", 1)[1]}),
        None,
    )
    if source is None:
        raise typer.BadParameter("Choose a name from templates list")
    path = active_projects(load_layout(ROOT)).get(source.project)
    if path is None:
        typer.echo("template source is not installed", err=True)
        raise typer.Exit(4)
    typer.echo(str(path))


@templates_app.command("versions")
def templates_versions(
    template: Annotated[str, typer.Argument(help="Trusted catalog alias.")],
) -> None:
    """List reviewed annotated versions; this read-only command does not fetch."""
    source = load_templates(ROOT).get(template)
    if source is None:
        raise typer.BadParameter("Choose a template alias from templates list")
    for version in source.metadata.versions:
        recommendation = (
            " recommended" if version.version == source.metadata.recommended_version else ""
        )
        typer.echo(f"{version.version} {version.tag} {version.commit}{recommendation}")
    if not source.metadata.versions:
        typer.echo("No approved tagged versions")


def _template_choice(sources: dict[str, TemplateSource], template: str | None) -> str:
    if template is None:
        if not sys.stdin.isatty():
            raise typer.BadParameter("Non-interactive use requires --template and --destination")
        typer.echo("Templates: " + ", ".join(sources))
        template = typer.prompt("Template")
    if template not in sources:
        raise typer.BadParameter("Choose a template alias from templates list")
    return template


def _run_template(
    operation: str,
    template: str | None,
    version: str | None,
    destination: Path | None,
    data: Path | None,
    defaults: bool,
    offline: bool,
) -> None:
    try:
        sources = load_templates(ROOT)
        selected = _template_choice(sources, template)
        if template is None and version is None:
            recommendation = sources[selected].metadata.recommended_version
            if recommendation is None:
                raise ValueError("No reviewed recommended version is available")
            typer.echo(
                "Versions: "
                + ", ".join(item.version for item in sources[selected].metadata.versions)
            )
            version = typer.prompt("Version", default=recommendation)
        if destination is None:
            if not sys.stdin.isatty():
                raise typer.BadParameter("Non-interactive use requires --destination")
            destination = Path(typer.prompt("Destination"))
        if not defaults and not sys.stdin.isatty():
            raise typer.BadParameter(
                "Non-interactive Copier use requires --defaults and optional --data"
            )
        answers: dict[str, JsonValue] = {}
        if data is not None:
            try:
                answers = TypeAdapter(dict[str, JsonValue]).validate_json(data.read_bytes())
            except ValidationError:
                raise ValueError("--data must contain a JSON object of template answers") from None
        plan = plan_template(
            sources,
            template=selected,
            version=version,
            destination=destination,
            operation="update" if operation == "update" else "create",
            offline=offline,
            defaults=defaults,
            data=answers,
        )
        result = execute_template(plan)
    except (ValueError, OSError, CopierError, subprocess.SubprocessError) as exc:
        typer.echo(f"templates {operation} failed: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"{result.status}: {result.destination}")
    typer.echo(f"template {result.template} {result.version} @ {result.source_commit}")
    typer.echo(f"cd {shlex.quote(str(result.destination))}")
    if not (result.destination / "uv.lock").is_file():
        typer.echo("uv lock")
    typer.echo("uv sync --locked\nmake check\nmake build")
    if result.template in {"fastapi", "service"}:
        typer.echo("make run")
    elif result.template == "typer":
        typer.echo(f"uv run --locked {shlex.quote(result.project_name)} --help")
    else:
        typer.echo(f"uv run --locked python -c 'import {result.project_name.lower()}'")
    typer.echo("Review and commit the generated project before updating it:")
    typer.echo(f"setup-system templates versions {result.template}")
    typer.echo(f"setup-system templates update --template {result.template} --destination .")
    if result.status == "conflicts":
        typer.echo(
            "Copier reported merge conflicts; review the working tree before committing.", err=True
        )
        raise typer.Exit(3)


@templates_app.command("create")
def templates_create(
    template: Annotated[str | None, typer.Option(help="Trusted catalog alias.")] = None,
    version: Annotated[
        str | None, typer.Option(help="Reviewed version; default is the catalog recommendation.")
    ] = None,
    destination: Annotated[
        Path | None, typer.Option(help="New or empty project directory.")
    ] = None,
    data: Annotated[
        Path | None, typer.Option(help="JSON file of Copier answers; never internal keys.")
    ] = None,
    defaults: Annotated[
        bool,
        typer.Option(help="Use defaults without prompts; supply required answers with --data."),
    ] = False,
    offline: Annotated[
        bool, typer.Option(help="Require prepared source cache; never fetch.")
    ] = False,
) -> None:
    """Create from a pinned source without installing, initializing Git or running tasks."""
    _run_template("create", template, version, destination, data, defaults, offline)


@templates_app.command("update")
def templates_update(
    template: Annotated[
        str | None, typer.Option(help="Trusted catalog alias recorded in the project.")
    ] = None,
    version: Annotated[
        str | None, typer.Option(help="Reviewed version; default is the catalog recommendation.")
    ] = None,
    destination: Annotated[Path | None, typer.Option(help="Existing clean Git project.")] = None,
    data: Annotated[Path | None, typer.Option(help="Optional JSON answer changes.")] = None,
    defaults: Annotated[
        bool, typer.Option(help="Use defaults for new questions without prompting.")
    ] = False,
    offline: Annotated[
        bool, typer.Option(help="Require prepared source cache; never fetch.")
    ] = False,
) -> None:
    """Run official Copier update, preserving its answers and reporting conflicts."""
    _run_template("update", template, version, destination, data, defaults, offline)


@templates_app.command("cache")
def templates_cache(
    template: Annotated[str, typer.Option(help="Trusted catalog alias.")],
    offline: Annotated[
        bool, typer.Option(help="Validate an existing cache without fetching.")
    ] = False,
) -> None:
    """Prepare all reviewed source versions for later offline copy and update."""
    try:
        plan = plan_template(
            load_templates(ROOT),
            template=template,
            version=None,
            destination=Path.cwd(),
            offline=offline,
        )
        path = prepare_cache(plan)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        typer.echo(f"templates cache failed: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"verified source cache: {path}")
