"""Private author profiles using native Git configuration and scoped backups."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from setup_system.diagnostics import PartialApplyError
from setup_system.domain.receipts import digest
from setup_system.domain.workspace_facts import GitIdentity
from setup_system.infrastructure.managed_files import apply_files
from setup_system.infrastructure.user_storage import inside_home

IDENTITY_KEYS = ("user.name", "user.email", "user.useConfigOnly", "user.signingKey")
SIGNING_KEYS = ("commit.gpgSign", "tag.gpgSign", "gpg.format")


def _git(*args: str, repo: Path | None = None, missing: bool = False) -> str:
    command = ["git"]
    if repo is not None:
        command += ["-C", str(repo)]
    result = subprocess.run(
        [*command, *args],
        text=True,
        capture_output=True,
        timeout=15,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        check=False,
    )
    if result.returncode and not (missing and result.returncode == 1):
        # Neither arguments nor stderr are safe: they may contain identity values.
        raise ValueError("Git configuration operation failed; no configuration values logged")
    return result.stdout.removesuffix("\n")


def exact_repository(repo: Path) -> Path:
    """Reject implicit parent discovery, bare repositories and linked worktrees."""
    repo = inside_home(repo)
    if not (repo / ".git").is_dir() or (repo / ".git").is_symlink():
        raise ValueError("Select an ordinary checkout with its own .git directory")
    if _git("rev-parse", "--show-toplevel", repo=repo) != str(repo):
        raise ValueError("Select the exact checkout root")
    return repo


def profile_path(name: str) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
        raise ValueError("Profile names use lowercase letters, digits, underscores or hyphens")
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return inside_home(config / "git/identities" / f"{name}.gitconfig")


def list_profiles() -> list[tuple[str, Path]]:
    folder = profile_path("default").parent
    profiles = []
    for path in sorted(folder.glob("*.gitconfig")):
        read_profile(path.stem)
        profiles.append((path.stem, path))
    return profiles


def _identity(values: dict[str, str]) -> GitIdentity:
    try:
        return GitIdentity(
            name=values.get("user.name", ""),
            email=values.get("user.email", ""),
            signing_key=values.get("user.signingKey"),
        )
    except ValueError:
        raise ValueError("A profile needs explicit, valid user.name and user.email") from None


def read_profile(name: str) -> GitIdentity:
    path = profile_path(name)
    if not path.is_file():
        raise ValueError(f"Profile does not exist: {name}")
    keys = _git("config", "--file", str(path), "--no-includes", "--name-only", "--list")
    allowed = {key.lower() for key in IDENTITY_KEYS}
    if any(key.lower() not in allowed for key in keys.splitlines()):
        raise ValueError(
            "Profiles may contain author fields only, without includes or auth settings"
        )
    values = {}
    for key in IDENTITY_KEYS:
        value = _git("config", "--file", str(path), "--no-includes", "--get-all", key, missing=True)
        if "\n" in value:
            raise ValueError("Duplicate or multiline identity values require review")
        if value:
            values[key] = value
    if values.get("user.useConfigOnly", "true").lower() != "true":
        raise ValueError("Profile user.useConfigOnly must be true when present")
    return _identity(values)


def inspect_repository(repo: Path) -> list[tuple[str, str]]:
    """Read only author/signing keys and their origins; never query all config."""
    repo = exact_repository(repo)
    result = []
    for key in (*IDENTITY_KEYS, *SIGNING_KEYS):
        output = _git(
            "config",
            "--includes",
            "--null",
            "--show-origin",
            "--show-scope",
            "--get-all",
            key,
            repo=repo,
            missing=True,
        )
        parts = output.removesuffix("\0").split("\0") if output else []
        if len(parts) % 3:
            raise ValueError("Unexpected Git configuration output; no values logged")
        rows = []
        for index in range(0, len(parts), 3):
            scope, origin, value = parts[index : index + 3]
            if key == "user.signingKey":
                value = "[configured; value hidden]"
            rows.append(
                "\t".join(
                    json.dumps(part, ensure_ascii=False)[1:-1] for part in (scope, origin, value)
                )
            )
        result.append((key, "\n".join(rows) or "unset"))
    overrides = [
        key
        for key in (
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
            "EMAIL",
            "GIT_CONFIG_COUNT",
            "GIT_CONFIG_PARAMETERS",
        )
        if key in os.environ
    ]
    if overrides:
        result.append(("environment", "review overrides: " + ", ".join(overrides)))
    return result


def _source_identity(repo: Path) -> GitIdentity:
    repo = exact_repository(repo)
    return _identity(
        {
            key: value
            for key in ("user.name", "user.email", "user.signingKey")
            if (value := _git("config", "--includes", "--get", key, repo=repo, missing=True))
        }
    )


def _hash(path: Path) -> str | None:
    path = inside_home(path)
    if path.exists() and not path.is_file():
        raise ValueError(f"Configuration target is not a regular file: {path}")
    return digest(path.read_bytes()) if path.exists() else None


def _verify_effective(repo: Path, edits: dict[Path, dict[str, str]]) -> None:
    for fields in edits.values():
        for key, value in fields.items():
            if _git("config", "--includes", "--get", key, repo=repo) != value:
                raise ValueError("Another configuration source overrides the local identity")


def _apply(
    edits: dict[Path, dict[str, str]], *, yes: bool, effective_repo: Path | None = None
) -> tuple[Path | None, list[str]]:
    """Render scoped edits with native Git and publish with the shared recovery adapter."""
    changed: dict[Path, dict[str, str]] = {}
    before: dict[Path, str | None] = {}
    for path, fields in edits.items():
        before[path] = _hash(path)
        pending = {}
        for key, value in fields.items():
            current = (
                _git("config", "--file", str(path), "--no-includes", "--get-all", key, missing=True)
                if path.exists()
                else ""
            )
            if current != value:
                pending[key] = value
        if pending:
            changed[path] = pending
    actions = [f"would update {path}: {', '.join(fields)}" for path, fields in changed.items()]
    if not changed or not yes:
        if not changed and effective_repo is not None:
            _verify_effective(effective_repo, edits)
        return None, actions or ["unchanged"]
    desired = {}
    with tempfile.TemporaryDirectory(prefix="setup-system-git-") as directory:
        for index, (path, fields) in enumerate(changed.items()):
            staged = Path(directory) / str(index)
            original = path.read_bytes() if path.exists() else None
            if (digest(original) if original is not None else None) != before[path]:
                raise ValueError(f"Configuration changed since planning: {path}")
            staged.write_bytes(original or b"")
            for key, value in fields.items():
                _git("config", "--file", str(staged), "--replace-all", "--", key, value)
                if (
                    _git("config", "--file", str(staged), "--no-includes", "--get-all", key)
                    != value
                ):
                    raise ValueError(f"Git configuration readback failed for {key}")
            desired[path] = staged.read_bytes()
    backup = apply_files(
        desired,
        modes={path: 0o600 for path in changed if before[path] is None},
        expected={path: before[path] for path in changed},
    )
    if effective_repo is not None:
        try:
            _verify_effective(effective_repo, edits)
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            raise PartialApplyError(
                f"Local Git identity was written but effective verification failed. "
                f"Inspect recovery operation {backup.name}."
            ) from error
    return backup, [action.replace("would update", "updated", 1) for action in actions]


def configure_profile(
    name: str, source_repo: Path, directories: list[Path], *, yes: bool = False
) -> tuple[Path | None, list[str]]:
    """Capture one confirmed Git identity and add defaults for reviewed directories."""
    identity = _source_identity(source_repo)
    target = profile_path(name)
    if target.exists() and read_profile(name) != identity:
        raise ValueError("Existing profile differs; preserve it and choose another explicit name")
    config = inside_home(Path(os.environ.get("GIT_CONFIG_GLOBAL", Path.home() / ".gitconfig")))
    fields = {}
    for directory in directories:
        directory = inside_home(directory)
        if not directory.is_dir() or any(char in str(directory) for char in "*?[]\\\n\r"):
            raise ValueError("Directory defaults need an existing literal directory")
        key = f"includeIf.gitdir:{directory}/.path"
        current = (
            _git("config", "--file", str(config), "--no-includes", "--get-all", key, missing=True)
            if config.exists()
            else ""
        )
        if current and current != str(target):
            raise ValueError(f"Existing directory identity include requires review: {directory}")
        fields[key] = str(target)
    return _apply({target: identity.fields(), config: fields}, yes=yes)


def use_profile(name: str, repo: Path, *, yes: bool = False) -> tuple[Path | None, list[str]]:
    """Set an explicit local author override, preserving unspecified signing settings."""
    repo = exact_repository(repo)
    identity = read_profile(name)
    if (
        _git("config", "--bool", "--get", "extensions.worktreeConfig", repo=repo, missing=True)
        == "true"
    ):
        raise ValueError("Per-worktree configuration requires a separate reviewed operation")
    return _apply({repo / ".git/config": identity.fields()}, yes=yes, effective_repo=repo)
