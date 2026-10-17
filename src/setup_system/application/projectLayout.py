"""Read-only catalog and Git-aware relocation planning for owned checkouts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ProjectState = Literal["existing", "planned", "sourcePending", "optional"]


@dataclass(frozen=True)
class Project:
    id: str
    target: Path
    legacyPath: Path | None
    origin: str | None
    state: ProjectState
    reviewOwner: bool


@dataclass(frozen=True)
class ProjectLayout:
    projectsRoot: Path
    ownerGroups: tuple[str, ...]
    projects: tuple[Project, ...]


@dataclass(frozen=True)
class GitSnapshot:
    head: str
    branch: str
    upstream: str
    outgoing: int
    origin: str
    gitMetadata: str
    worktrees: int
    gitlinks: int
    symlinks: int
    unmerged: int
    staged: int
    unstaged: int
    untracked: int
    trackedModeDigest: str
    indexDigest: str
    stagedDigest: str
    unstagedDigest: str
    localEnvironment: bool


@dataclass(frozen=True)
class MovePlan:
    project: Project
    source: Path | None
    status: str
    reason: str
    snapshot: GitSnapshot | None


def _homePath(value: str, home: Path) -> Path:
    if not value.startswith("$HOME/"):
        raise ValueError("Project paths must start with literal $HOME/")
    parts = value[6:].split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Project path has an unsafe component")
    return home.joinpath(*parts)


def loadLayout(root: Path, *, home: Path | None = None) -> ProjectLayout:
    """Load one declarative catalog without expanding arbitrary environment data."""
    home = Path.home() if home is None else home
    data = tomllib.loads((root / "manifests/projects.toml").read_text(encoding="utf-8"))
    if data.get("schemaVersion") != 1:
        raise ValueError("Unsupported project catalog schema")
    projectsRoot = _homePath(data["projectsRoot"], home)
    groups = tuple(data["ownerGroups"])
    if not groups or len(groups) != len(set(groups)):
        raise ValueError("Owner groups must be unique")
    if any(not re.fullmatch(r"[a-z][A-Za-z0-9]*", group) for group in groups):
        raise ValueError("Invalid owner group")
    projects: list[Project] = []
    seen: set[str] = set()
    paths: dict[Path, str] = {}
    for record in data["project"]:
        projectId = record["id"]
        parts = projectId.split("/")
        if (
            len(parts) != 2
            or parts[0] not in groups
            or not (re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", parts[1]) or parts[1] == ".github")
            or projectId in seen
        ):
            raise ValueError(f"Invalid or repeated project ID: {projectId}")
        seen.add(projectId)
        state = record["state"]
        if state not in {"existing", "planned", "sourcePending", "optional"}:
            raise ValueError(f"Invalid project state: {projectId}")
        legacy = _homePath(record["legacyPath"], home) if "legacyPath" in record else None
        target = projectsRoot.joinpath(*parts)
        if legacy == target:
            raise ValueError(f"Legacy and target paths are identical: {projectId}")
        for path in (target, legacy):
            if path is None:
                continue
            previous = paths.setdefault(path, projectId)
            if previous != projectId:
                raise ValueError(f"Project paths overlap: {previous} and {projectId}")
        projects.append(
            Project(
                projectId,
                target,
                legacy,
                record.get("origin"),
                state,
                record.get("reviewOwner", False),
            )
        )
    return ProjectLayout(projectsRoot, groups, tuple(projects))


def _git(path: Path, *args: str, required: bool = True) -> bytes:
    environment = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    result = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(path), *args],
        capture_output=True,
        timeout=15,
        check=False,
        env=environment,
    )
    if required and result.returncode:
        raise ValueError(f"Git inspection failed at {path}: {' '.join(args)}")
    return result.stdout if result.returncode == 0 else b""


def _symlinkComponent(path: Path) -> Path | None:
    current = path
    while True:
        if current.is_symlink():
            return current
        if current == current.parent:
            return None
        current = current.parent


def _exactGitRoot(path: Path) -> bool:
    if (
        _symlinkComponent(path) is not None
        or (path / ".git").is_symlink()
        or not path.is_dir()
        or not (path / ".git").exists()
    ):
        return False
    result = _git(path, "rev-parse", "--show-toplevel", required=False).decode().strip()
    return result != "" and Path(result) == path


def activeProjects(layout: ProjectLayout) -> dict[str, Path]:
    """Choose a single existing checkout per project, never a directory scan."""
    result: dict[str, Path] = {}
    for project in layout.projects:
        found = [
            path
            for path in (project.target, project.legacyPath)
            if path is not None and _exactGitRoot(path)
        ]
        if len(found) > 1:
            raise ValueError(f"Two active checkouts for {project.id}; review before opening")
        if found:
            result[project.id] = found[0]
    return result


def _snapshot(path: Path) -> GitSnapshot:
    metadata = path / ".git"
    staged = _git(path, "diff", "--no-ext-diff", "--no-textconv", "--cached", "--name-only", "-z")
    unstaged = _git(path, "diff", "--no-ext-diff", "--no-textconv", "--name-only", "-z")
    untracked = _git(path, "ls-files", "--others", "--exclude-standard", "-z")
    indexPath = Path(_git(path, "rev-parse", "--git-path", "index").decode().strip())
    if not indexPath.is_absolute():
        indexPath = path / indexPath
    modes = _git(path, "ls-files", "--stage", "-z")
    unmerged = _git(path, "ls-files", "-u", "-z")
    upstream = (
        _git(path, "rev-parse", "--abbrev-ref", "@{upstream}", required=False).decode().strip()
    )
    outgoing = (
        int(_git(path, "rev-list", "--count", "@{upstream}..HEAD").decode().strip())
        if upstream
        else 0
    )
    return GitSnapshot(
        head=_git(path, "rev-parse", "HEAD", required=False).decode().strip() or "UNBORN",
        branch=_git(path, "symbolic-ref", "--quiet", "--short", "HEAD", required=False)
        .decode()
        .strip()
        or "DETACHED",
        upstream=upstream or "none",
        outgoing=outgoing,
        origin=_git(path, "remote", "get-url", "origin", required=False).decode().strip() or "none",
        gitMetadata="file" if metadata.is_file() else "directory",
        worktrees=sum(
            line.startswith(b"worktree ")
            for line in _git(path, "worktree", "list", "--porcelain").splitlines()
        ),
        gitlinks=sum(record.startswith(b"160000 ") for record in modes.split(b"\0") if record),
        symlinks=sum(record.startswith(b"120000 ") for record in modes.split(b"\0") if record),
        unmerged=unmerged.count(b"\0"),
        staged=staged.count(b"\0"),
        unstaged=unstaged.count(b"\0"),
        untracked=untracked.count(b"\0"),
        trackedModeDigest=hashlib.sha256(modes).hexdigest(),
        indexDigest=hashlib.sha256(indexPath.read_bytes()).hexdigest()
        if indexPath.is_file()
        else "absent",
        stagedDigest=hashlib.sha256(
            _git(path, "diff", "--no-ext-diff", "--no-textconv", "--cached", "--binary")
        ).hexdigest(),
        unstagedDigest=hashlib.sha256(
            _git(path, "diff", "--no-ext-diff", "--no-textconv", "--binary")
        ).hexdigest(),
        localEnvironment=(path / ".venv").exists() or (path / "build").exists(),
    )


def _targetBlocker(
    layout: ProjectLayout, target: Path, *, includeTarget: bool = True
) -> str | None:
    paths = (
        (layout.projectsRoot, target.parent, target)
        if includeTarget
        else (layout.projectsRoot, target.parent)
    )
    for path in paths:
        symlink = _symlinkComponent(path)
        if symlink is not None:
            return f"symlink at {symlink}"
        if path.exists() and not path.is_dir():
            return f"non-directory at {path}"
        metadata = path / ".git"
        if metadata.is_symlink() or (
            metadata.exists() and (not metadata.is_dir() or any(metadata.iterdir()))
        ):
            return f"parent or destination contains Git metadata: {path}"
    return None


def planMigration(
    layout: ProjectLayout, *, currentDirectory: Path | None = None
) -> tuple[MovePlan, ...]:
    """Inspect sources and destinations; never create directories or change Git state."""
    currentDirectory = Path.cwd() if currentDirectory is None else currentDirectory
    result: list[MovePlan] = []
    for project in layout.projects:
        source = project.legacyPath
        target = project.target
        if source is not None and source.is_symlink():
            result.append(MovePlan(project, source, "blocked", "source is a symlink", None))
            continue
        if target.exists() and source is not None and source.exists():
            result.append(
                MovePlan(project, source, "blocked", "source and destination both exist", None)
            )
            continue
        if target.exists():
            parentBlocker = _targetBlocker(layout, target, includeTarget=False)
            if parentBlocker is not None:
                result.append(MovePlan(project, target, "blocked", parentBlocker, None))
                continue
            if _exactGitRoot(target):
                snapshot = _snapshot(target)
                status = (
                    "placed"
                    if not project.origin or snapshot.origin == project.origin
                    else "review"
                )
                reason = (
                    "target checkout present"
                    if status == "placed"
                    else "origin differs from catalog"
                )
                if project.reviewOwner and status == "placed":
                    status, reason = "review", "remote owner differs from proposed owner group"
                result.append(MovePlan(project, target, status, reason, snapshot))
            else:
                result.append(
                    MovePlan(
                        project,
                        target,
                        "blocked",
                        "destination exists without exact Git root",
                        None,
                    )
                )
            continue
        if source is None or not source.exists():
            status = "sourcePending" if project.state == "existing" else project.state
            result.append(MovePlan(project, source, status, "no local source checkout", None))
            continue
        blocker = _targetBlocker(layout, target)
        if blocker is not None:
            result.append(MovePlan(project, source, "blocked", blocker, None))
            continue
        if not _exactGitRoot(source):
            result.append(
                MovePlan(project, source, "blocked", "source is not an exact Git root", None)
            )
            continue
        snapshot = _snapshot(source)
        reason = "private full-tree backup and reference review required before move"
        status = "backupRequired"
        if project.origin and snapshot.origin != project.origin:
            status, reason = "review", "origin differs from catalog"
        elif project.reviewOwner:
            status, reason = "review", "remote owner differs from proposed owner group"
        elif snapshot.gitMetadata == "file" or snapshot.worktrees != 1:
            status, reason = "review", "linked or additional worktree needs Git-aware move/repair"
        elif snapshot.gitlinks:
            status, reason = "review", "submodule needs separate path and Git metadata review"
        elif snapshot.unmerged:
            status, reason = "review", "unmerged index entries need preservation review"
        elif snapshot.head == "UNBORN":
            status, reason = "review", "unborn Git branch has no HEAD baseline for relocation"
        elif currentDirectory == source or source in currentDirectory.parents:
            status, reason = "deferredActive", "current process is inside this checkout"
        else:
            parent = target.parent
            while not parent.exists():
                parent = parent.parent
            if source.stat().st_dev != parent.stat().st_dev:
                status, reason = "review", "cross-filesystem copy/verify/switch required"
        result.append(MovePlan(project, source, status, reason, snapshot))
    return tuple(result)


def formatMigrationPlan(layout: ProjectLayout, moves: tuple[MovePlan, ...]) -> str:
    lines = [
        f"projectsRoot: {layout.projectsRoot}",
        "Read-only relocation plan; no directories or Git metadata changed.",
        "References to review after each move: agent scopes, picker, editor/tmux, "
        "editable installs, template sources and project docs.",
    ]
    for move in moves:
        source = move.source or "absent"
        lines.append(f"{move.project.id}: {source} -> {move.project.target} [{move.status}]")
        backup = (
            "required"
            if move.status in {"backupRequired", "deferredActive", "review"}
            else "notApplicable"
        )
        lines.append(f"  reason={move.reason}; backup={backup}; verification=notRun")
        if move.snapshot is not None:
            git = move.snapshot
            lines.append(
                f"  git={git.head[:12]} branch={git.branch} upstream={git.upstream} "
                f"outgoing={git.outgoing} "
                f"metadata={git.gitMetadata} worktrees={git.worktrees} gitlinks={git.gitlinks} "
                f"symlinks={git.symlinks} unmerged={git.unmerged} "
                f"staged={git.staged} unstaged={git.unstaged} untracked={git.untracked} "
                f"localEnvironment={'present' if git.localEnvironment else 'absent'}"
            )
            lines.append(
                f"  sha256 index={git.indexDigest} modes={git.trackedModeDigest} "
                f"stagedPatch={git.stagedDigest} "
                f"unstagedPatch={git.unstagedDigest}"
            )
    return "\n".join(lines)


def _fileDigest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backupRecord(manifestPath: Path, source: Path, snapshot: GitSnapshot) -> None:
    """Check the private full-tree archive before changing a checkout path."""
    manifest = json.loads(manifestPath.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != 1:
        raise ValueError("Unsupported relocation backup manifest")
    matches = [record for record in manifest.get("repos", []) if record.get("root") == str(source)]
    if len(matches) != 1:
        raise ValueError("Backup has no unique record for this exact source")
    record = matches[0]
    archiveName = record["archive"]
    if Path(archiveName).name != archiveName or not archiveName.endswith(".tar"):
        raise ValueError("Unsafe backup archive name")
    archive = manifestPath.parent / archiveName
    if archive.is_symlink() or not archive.is_file() or _fileDigest(archive) != record["sha256"]:
        raise ValueError("Backup archive is missing or its digest differs")
    if (
        record["head"] != snapshot.head
        or record["branch"] != snapshot.branch
        or ("indexSha256" in record and record["indexSha256"] != _fileDigest(source / ".git/index"))
        or record["refs"] != _git(source, "show-ref").decode()
        or record["status"]
        != _git(source, "status", "--porcelain=v1", "--untracked-files=all").decode()
    ):
        raise ValueError("Checkout changed after backup; take a fresh snapshot")


def applyMigration(
    layout: ProjectLayout,
    projectId: str,
    *,
    backupManifest: Path,
    resultDirectory: Path,
    currentDirectory: Path | None = None,
    acceptOwnerReview: bool = False,
) -> Path:
    """Atomically relocate one ordinary checkout after a verified private backup."""
    matches = [
        move
        for move in planMigration(layout, currentDirectory=currentDirectory)
        if move.project.id == projectId
    ]
    if len(matches) != 1:
        raise ValueError("Choose one exact project ID from the catalog")
    move = matches[0]
    if move.status == "placed":
        return move.project.target
    if move.status == "review" and not (
        acceptOwnerReview
        and move.project.reviewOwner
        and move.reason == "remote owner differs from proposed owner group"
    ):
        raise ValueError(f"Relocation needs review: {move.reason}")
    if (
        move.status not in {"backupRequired", "review"}
        or move.source is None
        or move.snapshot is None
    ):
        raise ValueError(f"Relocation is not ready: {move.status}: {move.reason}")
    source, target, before = move.source, move.project.target, move.snapshot
    if not target.parent.is_dir() or target.exists() or target.is_symlink():
        raise ValueError("Destination owner directory must exist and target must be absent")
    if source.stat().st_dev != target.parent.stat().st_dev:
        raise ValueError("Cross-filesystem move requires a separate copy/verify/switch procedure")
    if (
        before.gitMetadata != "directory"
        or before.worktrees != 1
        or before.gitlinks
        or before.unmerged
    ):
        raise ValueError("Linked worktree, submodule or unmerged index needs separate Git review")
    if move.project.origin is not None and before.origin != move.project.origin:
        raise ValueError("Origin differs from catalog")
    _backupRecord(backupManifest, source, before)
    sourceInode = source.stat().st_ino
    beforeRefs = _git(source, "show-ref")
    beforeStatus = _git(source, "status", "--porcelain=v1", "--untracked-files=all")
    resultDirectory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if resultDirectory.is_symlink() or (resultDirectory.stat().st_mode & 0o077):
        raise ValueError("Relocation result directory must be private")
    resultPath = resultDirectory / f"{projectId.replace('/', '-')}-{int(time.time())}.json"
    if resultPath.exists():
        raise ValueError("Relocation result path already exists")
    result = {
        "project": projectId,
        "source": str(source),
        "target": str(target),
        "backupManifest": str(backupManifest),
        "head": before.head,
        "branch": before.branch,
        "origin": before.origin,
        "status": "moving",
    }
    resultPath.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    resultPath.chmod(0o600)
    os.rename(source, target)
    after = _snapshot(target)
    verified = (
        target.stat().st_ino == sourceInode
        and not source.exists()
        and before == after
        and beforeRefs == _git(target, "show-ref")
        and beforeStatus == _git(target, "status", "--porcelain=v1", "--untracked-files=all")
    )
    result["status"] = "verified" if verified else "verificationFailed"
    resultPath.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if not verified:
        raise RuntimeError(f"Moved checkout needs manual recovery; inspect {resultPath}")
    return resultPath


def prepareOwnerDirectories(layout: ProjectLayout) -> tuple[Path, ...]:
    """Create only the root and owner groups after an explicit Debian CLI action."""
    paths = (layout.projectsRoot, *(layout.projectsRoot / group for group in layout.ownerGroups))
    for path in paths:
        if _symlinkComponent(path) is not None or (path.exists() and not path.is_dir()):
            raise ValueError(f"Unsafe layout path: {path}")
        if (path / ".git").exists():
            raise ValueError(f"Layout parent cannot be a Git repository: {path}")
    for path in paths:
        path.mkdir(exist_ok=True)
    return paths
