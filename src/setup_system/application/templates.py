"""Plan trusted Copier operations and execute them from a verified Git cache."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import shutil
import subprocess
import tempfile
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import copier
import yaml
from plumbum import local
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

from setup_system.application.project_catalog import (
    ProjectCatalog,
    TemplateMetadata,
    TemplateVersion,
)


class TemplateSource(BaseModel):
    """A canonical GitHub source declared by the shared project catalog."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    project: str
    url: str
    metadata: TemplateMetadata


class TemplatePlan(BaseModel):
    """Explicit inputs to one copy or update; no filesystem work during construction."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    operation: Literal["create", "update"]
    source: TemplateSource
    version: TemplateVersion
    destination: Path
    cache_root: Path
    offline: bool = False
    defaults: bool = False
    data: dict[str, JsonValue] = Field(default_factory=dict)


class TemplateResult(BaseModel):
    """Identity and next actions after the official generator returns."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    destination: Path
    template: str
    version: str
    source_commit: str
    project_name: str
    status: Literal["created", "updated", "conflicts"]


class _AnswersSource(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)
    source: str
    commit: str


def load_templates(root: Path) -> dict[str, TemplateSource]:
    """Use the existing catalog; never discover or accept arbitrary remote sources."""
    try:
        catalog = ProjectCatalog.model_validate(
            tomllib.loads((root / "manifests/projects.toml").read_text(encoding="utf-8"))
        )
    except (ValidationError, tomllib.TOMLDecodeError) as error:
        raise ValueError("Invalid project/template catalog") from error
    sources: dict[str, TemplateSource] = {}
    for record in catalog.project:
        metadata = record.template
        if metadata is None:
            continue
        parts = record.id.split("/")
        if len(parts) != 2 or parts[0] not in catalog.owner_groups:
            raise ValueError("Template must belong to a declared project owner")
        if not all(part.isalnum() and part.isascii() for part in parts):
            raise ValueError("Unsafe template project ID")
        url = f"https://github.com/{record.id}.git"
        if record.origin not in {url, f"git@github.com:{record.id}.git"}:
            raise ValueError("Template origin must match its canonical GitHub project")
        if metadata.alias in sources:
            raise ValueError("Duplicate template alias")
        sources[metadata.alias] = TemplateSource(project=record.id, url=url, metadata=metadata)
    return sources


def plan_template(
    sources: dict[str, TemplateSource],
    *,
    template: str,
    version: str | None,
    destination: Path,
    operation: Literal["create", "update"] = "create",
    offline: bool = False,
    defaults: bool = False,
    data: dict[str, JsonValue] | None = None,
    cache_root: Path | None = None,
) -> TemplatePlan:
    """Resolve only a reviewed version; latest/main and arbitrary refs are rejected."""
    if template not in sources:
        raise ValueError("Choose a template alias from templates list")
    source = sources[template]
    selected = version if version is not None else source.metadata.recommended_version
    match = next((item for item in source.metadata.versions if item.version == selected), None)
    if match is None:
        raise ValueError("No reviewed version; choose a version from templates versions")
    if any(key.startswith("_") for key in data or {}):
        raise ValueError("Internal Copier answers cannot be supplied")
    cache = cache_root or Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return TemplatePlan(
        operation=operation,
        source=source,
        version=match,
        destination=destination.expanduser().absolute(),
        cache_root=cache / "setup-system/templates",
        offline=offline,
        defaults=defaults,
        data=data or {},
    )


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(repository), *args],
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        # Git stderr may include credentials from a user's credential helper or proxy.
        raise ValueError(f"Git operation failed ({args[0]}, exit {result.returncode})")
    return result.stdout.strip()


def _no_symlinks(path: Path) -> None:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Template destinations and cache paths cannot traverse symlinks")


def _fetch_ref(repository: Path, source: str, tag: str) -> None:
    _git(
        repository,
        "fetch",
        "--no-tags",
        "--no-recurse-submodules",
        source,
        f"refs/tags/{tag}:refs/tags/{tag}",
    )


def _validate_cache(repository: Path, source: TemplateSource) -> None:
    for version in source.metadata.versions:
        ref = f"refs/tags/{version.tag}"
        if _git(repository, "cat-file", "-t", ref) != "tag":
            raise ValueError(f"Template version {version.version} requires an annotated tag")
        if _git(repository, "rev-parse", f"{ref}^{{commit}}") != version.commit:
            raise ValueError(f"Template tag/commit mismatch for {version.version}")
        try:
            metadata = tomllib.loads(_git(repository, "show", f"{version.commit}:pyproject.toml"))
            actual_version = metadata["project"]["version"]
        except (KeyError, TypeError, tomllib.TOMLDecodeError) as error:
            raise ValueError("Template version metadata is missing or invalid") from error
        if actual_version != version.version:
            raise ValueError(f"Template package/tag version mismatch for {version.version}")
    _git(repository, "fsck", "--full", "--no-reflogs")


def _cached_repository(destination: Path, source: TemplateSource, identity: bytes) -> Path | None:
    repository = destination / "source.git"
    manifest = destination / "catalog.json"
    _no_symlinks(repository)
    _no_symlinks(manifest)
    if not repository.is_dir():
        return None
    if not manifest.is_file() or manifest.read_bytes() != identity:
        raise ValueError("Template cache identity is corrupt")
    _validate_cache(repository, source)
    return repository


def prepare_cache(plan: TemplatePlan) -> Path:
    """Atomically prepare an immutable catalog snapshot; offline never fetches."""
    identity = plan.source.model_dump_json().encode()
    key = hashlib.sha256(identity).hexdigest()
    destination = plan.cache_root / plan.source.metadata.alias / key
    repository = _cached_repository(destination, plan.source, identity)
    if repository is not None:
        return repository
    if plan.offline:
        raise ValueError("Offline template source cache is missing; run templates cache online")
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock_path = destination.parent / ".prepare.lock"
    _no_symlinks(lock_path)
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        repository = _cached_repository(destination, plan.source, identity)
        if repository is not None:
            return repository
        temporary = Path(tempfile.mkdtemp(prefix=".prepare-", dir=destination.parent))
        try:
            staging = temporary / "source.git"
            _git(temporary, "init", "--bare", str(staging))
            for version in plan.source.metadata.versions:
                _fetch_ref(staging, plan.source.url, version.tag)
            _validate_cache(staging, plan.source)
            (temporary / "catalog.json").write_bytes(identity)
            temporary.rename(destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return destination / "source.git"


@contextlib.contextmanager
def _cached_source(source: str, repository: Path) -> Iterator[None]:
    """Limit Copier's Git access to this cache without changing any Git config file."""
    values = {
        "url." + repository.as_uri() + ".insteadOf": source,
        "core.hooksPath": "/dev/null",
    }
    old = dict(os.environ)
    try:
        count = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
        for index, (key, value) in enumerate(values.items(), count):
            os.environ[f"GIT_CONFIG_KEY_{index}"] = key
            os.environ[f"GIT_CONFIG_VALUE_{index}"] = value
        os.environ["GIT_CONFIG_COUNT"] = str(count + len(values))
        os.environ["GIT_ALLOW_PROTOCOL"] = "file"
        os.environ["GIT_TERMINAL_PROMPT"] = "0"
        with local.env(**dict(os.environ)):
            yield
    finally:
        os.environ.clear()
        os.environ.update(old)


def _read_answers(destination: Path) -> dict[str, JsonValue]:
    path = destination / ".copier-answers.yml"
    if path.is_symlink() or not path.is_file():
        raise ValueError("Copier answers must be a regular file inside the project")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Copier answers must contain a mapping")
        return TypeAdapter(dict[str, JsonValue]).validate_python(data)
    except (OSError, ValueError, yaml.YAMLError):
        raise ValueError("Valid Copier answers are required") from None


def _validate_destination(plan: TemplatePlan) -> None:
    destination = plan.destination
    _no_symlinks(destination)
    if plan.operation == "create":
        if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
            raise ValueError("Destination must be a new or empty directory")
        return
    if not (destination / ".git").exists():
        raise ValueError("Copier update requires an existing clean Git project")
    # Compare the real root only after rejecting every original symlink component.
    if Path(_git(destination, "rev-parse", "--show-toplevel")) != destination.resolve():
        raise ValueError("Update destination must be the exact Git project root")
    if _git(destination, "status", "--porcelain"):
        raise ValueError("Commit or separately preserve project changes before Copier update")
    try:
        raw = _read_answers(destination)
        answers = _AnswersSource.model_validate(
            {"source": raw["_src_path"], "commit": raw["_commit"]}
        )
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as error:
        raise ValueError("Valid Copier answers are required for update") from error
    allowed = {item.tag for item in plan.source.metadata.versions}
    allowed.update(item.commit for item in plan.source.metadata.versions)
    if answers.source != plan.source.url or answers.commit not in allowed:
        raise ValueError("Project answers do not match a reviewed template source/version")


def execute_template(plan: TemplatePlan) -> TemplateResult:
    """Call official Copier, with tasks and unsafe extensions disabled, without commits."""
    _validate_destination(plan)
    repository = prepare_cache(plan)
    _validate_destination(plan)
    destination = plan.destination.resolve()
    with _cached_source(plan.source.url, repository):
        settings = copier.Settings(defaults={}, trust=[])
        if plan.operation == "create":
            copier.run_copy(
                plan.source.url,
                destination,
                data=plan.data,
                vcs_ref=plan.version.commit,
                defaults=plan.defaults,
                settings=settings,
                skip_tasks=True,
                unsafe=False,
            )
        else:
            copier.run_update(
                destination,
                data=plan.data,
                vcs_ref=plan.version.commit,
                defaults=plan.defaults,
                settings=settings,
                skip_tasks=True,
                unsafe=False,
                overwrite=True,
                skip_answered=True,
            )
    conflicts = bool(
        plan.operation == "update" and _git(destination, "diff", "--name-only", "--diff-filter=U")
    )
    remembered = _read_answers(destination)
    identity_key = "service_name" if plan.source.metadata.alias == "service" else "projectName"
    project_name = remembered.get(identity_key, "")
    if (
        not isinstance(project_name, str)
        or not project_name.isascii()
        or not project_name.isidentifier()
    ):
        raise ValueError(f"Generated answers require a valid {identity_key}")
    return TemplateResult(
        destination=destination,
        template=plan.source.metadata.alias,
        version=plan.version.version,
        source_commit=plan.version.commit,
        project_name=project_name,
        status="conflicts"
        if conflicts
        else ("created" if plan.operation == "create" else "updated"),
    )
