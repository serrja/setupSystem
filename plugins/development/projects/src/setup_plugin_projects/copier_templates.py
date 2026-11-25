"""Run official Copier against an authenticated local Git cache."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Iterator
from pathlib import Path

import yaml
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter

from setup_plugin_projects.models import (
    TemplateMetadata,
    TemplateVersion,
    python_version_for_git_tag,
)
from setup_plugin_projects.template_models import (
    TemplatePlan,
    TemplateResult,
    TemplateSource,
    answer_values,
)
from setup_system.diagnostics import PartialApplyError
from setup_system.infrastructure.processes import run_command
from setup_system.infrastructure.user_storage import safe_file


class _AnswersSource(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)
    source: str
    commit: str


def _git_environment() -> dict[str, str]:
    # Public sources never need the caller's credentials, proxy or Git settings.
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": "/dev/null",
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_ALLOW_PROTOCOL": "file:https",
    }


def _git(repository: Path, *args: str) -> str:
    result = run_command(
        [
            "git",
            "-c",
            "http.followRedirects=false",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
            str(repository),
            *args,
        ],
        discard_output=args[0] in {"fetch", "init", "fsck"},
        timeout=120,
        check=False,
        env=_git_environment(),
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
            # Shared releases allocate the native version after the source commit.
            # Its immutable annotated tag binds that allocation to this exact tree.
            annotation = _git(repository, "cat-file", "-p", ref).partition("\n\n")[2]
            try:
                record = json.loads(annotation)
            except ValueError:
                record = {}
            if not isinstance(record, dict) or any(
                record.get(k) != v
                for k, v in {
                    "repository": source.project,
                    "sha": version.commit,
                    "version": version.tag[1:],
                    "pythonVersion": version.version,
                    "contractSchema": "h02-python/v1",
                }.items()
            ):
                raise ValueError(f"Template package/tag version mismatch for {version.version}")
    _git(repository, "fsck", "--full", "--no-reflogs")


MAX_CACHE_IDENTITY = 2 * 1024 * 1024


def _cache_identity(path: Path) -> bytes:
    safe_file(path, os.getuid())
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o022
            or info.st_size > MAX_CACHE_IDENTITY
        ):
            raise ValueError("Unsafe or oversized template cache metadata")
        raw = stream.read(MAX_CACHE_IDENTITY + 1)
    if len(raw) > MAX_CACHE_IDENTITY:
        raise ValueError("Oversized template cache metadata")
    return raw


def _cached_repository(destination: Path, source: TemplateSource, identity: bytes) -> Path | None:
    repository = destination / "source.git"
    manifest = destination / "catalog.json"
    _no_symlinks(repository)
    _no_symlinks(manifest)
    if not repository.is_dir():
        return None
    if not manifest.is_file() or _cache_identity(manifest) != identity:
        raise ValueError("Template cache identity is corrupt")
    _validate_cache(repository, source)
    return repository


def _cached_sources(root: Path, source: TemplateSource) -> Iterator[tuple[TemplateSource, Path]]:
    parent = root / source.metadata.alias
    _no_symlinks(parent)
    for path in sorted(parent.glob("*/catalog.json")):
        _no_symlinks(path)
        raw = _cache_identity(path)
        if hashlib.sha256(raw).hexdigest() != path.parent.name:
            raise ValueError("Template cache identity is corrupt")
        cached = TemplateSource.model_validate_json(raw)
        if (cached.project, cached.url, cached.metadata.alias) != (
            source.project,
            source.url,
            source.metadata.alias,
        ):
            raise ValueError("Template cache source substitution")
        repo = _cached_repository(path.parent, cached, raw)
        if repo is not None:
            yield cached, repo


def available_versions(
    source: TemplateSource, cache: Path, *, offline: bool
) -> tuple[TemplateVersion, ...]:
    if source.metadata.versions:
        return tuple(
            sorted(source.metadata.versions, key=lambda v: Version(v.version), reverse=True)
        )
    found: dict[str, TemplateVersion] = {}
    if offline:
        rows = [v for cached, _ in _cached_sources(cache, source) for v in cached.metadata.versions]
    else:
        result = run_command(
            [
                "git",
                "-C",
                "/",
                "-c",
                "http.followRedirects=false",
                "ls-remote",
                "--tags",
                source.url,
                "refs/tags/v*",
            ],
            env=_git_environment(),
            timeout=60,
            check=False,
        )
        if result.returncode:
            raise ValueError("Cannot read annotated releases from the catalog GitHub project")
        rows = []
        for line in result.stdout.splitlines():
            sha, ref = line.split("\t", 1)
            if not ref.endswith("^{}"):
                continue
            tag = ref.removeprefix("refs/tags/").removesuffix("^{}")
            try:
                native = python_version_for_git_tag(tag)
            except ValueError:
                continue
            rows.append(TemplateVersion(version=native, tag=tag, commit=sha))
    for version in rows:
        if version.version in found and version != found[version.version]:
            raise ValueError("Conflicting immutable tags for one template version")
        found[version.version] = version
    return tuple(sorted(found.values(), key=lambda v: Version(v.version), reverse=True))


def selected_source(
    source: TemplateSource,
    cache: Path,
    version: str | None,
    destination: Path,
    *,
    update: bool,
    offline: bool,
) -> TemplateSource:
    rows = available_versions(source, cache, offline=offline)
    stable = next((row for row in rows if not Version(row.version).is_prerelease), None)
    target = next((r for r in rows if version in (r.version, r.tag)), None) if version else stable
    if target is None:
        raise ValueError(
            "Requested annotated template version is unavailable; inspect releases or cache online"
        )
    selected = [target]
    if update:
        answers = _read_answers(destination)
        original = next((r for r in rows if answers.get("_commit") in (r.tag, r.commit)), None)
        if original is None or answers.get("_src_path") != source.url:
            raise ValueError("Project answers do not match a known version of this template")
        if original != target:
            selected.append(original)
    return TemplateSource(
        project=source.project,
        url=source.url,
        metadata=TemplateMetadata(
            alias=source.metadata.alias, recommendedVersion=target.version, versions=tuple(selected)
        ),
    )


def cached(plan: TemplatePlan) -> bool:
    raw = plan.source.model_dump_json().encode()
    location = plan.cache_root / plan.source.metadata.alias / hashlib.sha256(raw).hexdigest()
    return _cached_repository(location, plan.source, raw) is not None


def prepare_cache(plan: TemplatePlan) -> Path:
    """Atomically prepare an immutable catalog snapshot; offline never fetches."""
    identity = plan.source.model_dump_json().encode()
    key = hashlib.sha256(identity).hexdigest()
    destination = plan.cache_root / plan.source.metadata.alias / key
    repository = _cached_repository(destination, plan.source, identity)
    if repository is not None:
        return repository
    cached_versions: dict[TemplateVersion, Path] = {}
    if plan.offline:
        for saved, repo in _cached_sources(plan.cache_root, plan.source):
            cached_versions.update((row, repo) for row in saved.metadata.versions)
        if any(row not in cached_versions for row in plan.source.metadata.versions):
            raise ValueError(
                "Offline template source cache is missing; prepare the template cache online"
            )
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock_path = destination.parent / ".prepare.lock"
    _no_symlinks(lock_path)
    safe_file(lock_path, os.getuid())
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(descriptor, "r+b") as lock:
        info = os.fstat(lock.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o022
        ):
            raise ValueError("Unsafe template cache lock")
        fcntl.flock(lock, fcntl.LOCK_EX)
        repository = _cached_repository(destination, plan.source, identity)
        if repository is not None:
            return repository
        temporary = Path(tempfile.mkdtemp(prefix=".prepare-", dir=destination.parent))
        try:
            staging = temporary / "source.git"
            _git(temporary, "init", "--bare", "--template=", str(staging))
            for version in plan.source.metadata.versions:
                if version in cached_versions:
                    _git(
                        staging,
                        "fetch",
                        "--no-tags",
                        str(cached_versions[version]),
                        f"refs/tags/{version.tag}:refs/tags/{version.tag}",
                    )
                else:
                    _fetch_ref(staging, plan.source.url, version.tag)
            _validate_cache(staging, plan.source)
            manifest = temporary / "catalog.json"
            manifest.write_bytes(identity)
            manifest.chmod(0o600)
            temporary.rename(destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return destination / "source.git"


class CopierUnavailableError(ValueError):
    """The template action needs the supported native Copier client."""


def copier_executable() -> Path:
    paths = (
        Path(sys.executable).with_name("copier"),
        Path.home() / ".local/bin/copier",
        Path("/usr/bin/copier"),
    )
    for path in paths:
        if not path.is_file() or not os.access(path, os.X_OK):
            continue
        info = path.resolve(strict=True).stat()
        if info.st_uid not in {0, os.getuid()} or info.st_mode & 0o022:
            raise CopierUnavailableError(
                "Copier must be owned by the user or administrator and protected against edits."
            )
        result = run_command(
            [str(path), "--version"],
            timeout=10,
            check=False,
            env=_git_environment(),
            discard_stderr=True,
            max_output_bytes=1024,
        )
        words = result.stdout.strip().split()
        if (
            result.returncode == 0
            and len(words) == 2
            and words[0] == "copier"
            and Version("9.10") <= Version(words[1]) < Version("10")
        ):
            return path
        raise CopierUnavailableError(
            "Install a compatible official Copier 9 release (9.10 or newer)."
        )
    raise CopierUnavailableError(
        "Install the official Copier 9 CLI (9.10 or newer) for template creation and updates."
    )


def _run_copier(plan: TemplatePlan, repository: Path, destination: Path) -> None:
    # Copier uses real Git locally. Override its one reviewed HTTPS origin to
    # the checked cache and disable all network transports, tasks and user trust.
    with tempfile.TemporaryDirectory(prefix="setup-copier-") as temporary:
        folder = Path(temporary)
        data = folder / "answers.json"
        data.write_text(json.dumps(answer_values(plan.data), allow_nan=False))
        settings = folder / "settings.yml"
        settings.write_text("defaults: {}\ntrust: []\n")
        env = _git_environment()
        env.update(
            HOME=str(folder),
            GIT_CONFIG_COUNT="2",
            GIT_CONFIG_KEY_0="url." + repository.as_uri() + ".insteadOf",
            GIT_CONFIG_VALUE_0=plan.source.url,
            GIT_CONFIG_KEY_1="core.hooksPath",
            GIT_CONFIG_VALUE_1="/dev/null",
            GIT_ALLOW_PROTOCOL="file",
            COPIER_SETTINGS_PATH=str(settings),
        )
        argv = [
            str(copier_executable()),
            "copy" if plan.operation == "create" else "update",
            "--defaults",
            "--skip-tasks",
            "--vcs-ref",
            plan.version.commit,
            "--data-file",
            str(data),
        ]
        if plan.operation == "create":
            argv.append(plan.source.url)
        else:
            argv += ["--skip-answered"]
        before_head = _git(destination, "rev-parse", "HEAD") if plan.operation == "update" else None
        try:
            result = run_command(
                [*argv, str(destination)],
                env=env,
                stdin=subprocess.DEVNULL,
                discard_output=True,
                timeout=300,
                check=False,
            )
            if not result.returncode:
                return
            reason = f"exit {result.returncode}"
        except subprocess.TimeoutExpired:
            reason = "timed out"
        except KeyboardInterrupt:
            reason = "interrupted"
        try:
            _no_symlinks(destination)
            if before_head is None:
                changed = destination.exists() and (
                    not destination.is_dir() or any(destination.iterdir())
                )
            else:
                changed = (
                    not destination.is_dir()
                    or _git(destination, "rev-parse", "HEAD") != before_head
                    or bool(_git(destination, "status", "--porcelain"))
                )
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise PartialApplyError(
                "Could not verify project changes after Copier stopped"
            ) from error
        if changed:
            raise PartialApplyError("Copier stopped with project changes; inspect the working tree")
        raise ValueError(f"Copier {plan.operation} failed ({reason}); inspect the project")


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


def validate_destination(plan: TemplatePlan) -> None:
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
    validate_destination(plan)
    repository = prepare_cache(plan)
    validate_destination(plan)
    destination = plan.destination.resolve()
    _run_copier(plan, repository, destination)
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
