"""Private author profiles using native Git configuration and scoped backups."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from setup_system.storage import atomic_write, new_backup, restore, state_root

IDENTITY_KEYS = ("user.name", "user.email", "user.useConfigOnly", "user.signingKey")
SIGNING_KEYS = ("commit.gpgSign", "tag.gpgSign", "gpg.format")


class GitIdentity(BaseModel):
    """Only author fields belong to a profile; authentication stays with Git."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1)
    email: str = Field(min_length=1)
    signing_key: str | None = None

    @field_validator("name", "email", "signing_key")
    @classmethod
    def single_line(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or any(ord(c) < 32 for c in value)):
            raise ValueError("Identity fields must be nonempty single-line values")
        return value

    def fields(self) -> dict[str, str]:
        values = {"user.name": self.name, "user.email": self.email, "user.useConfigOnly": "true"}
        if self.signing_key is not None:
            values["user.signingKey"] = self.signing_key
        return values


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


def _safe_path(path: Path) -> Path:
    candidate = path.expanduser().absolute()
    home = Path.home().resolve()
    if not candidate.is_relative_to(home) or ".." in candidate.parts:
        raise ValueError("Configuration paths must stay inside the current home")
    for component in (candidate, *candidate.parents):
        if component == home:
            break
        if component.is_symlink():
            raise ValueError(f"Symlink traversal requires a separate review: {component}")
    return candidate


def exact_repository(repo: Path) -> Path:
    """Reject implicit parent discovery, bare repositories and linked worktrees."""
    repo = _safe_path(repo)
    if not (repo / ".git").is_dir() or (repo / ".git").is_symlink():
        raise ValueError("Select an ordinary checkout with its own .git directory")
    if _git("rev-parse", "--show-toplevel", repo=repo) != str(repo):
        raise ValueError("--repo must identify the exact checkout root")
    return repo


def profile_path(name: str) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
        raise ValueError("Profile names use lowercase letters, digits, underscores or hyphens")
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return _safe_path(config / "git/identities" / f"{name}.gitconfig")


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
    path = _safe_path(path)
    if path.exists() and not path.is_file():
        raise ValueError(f"Configuration target is not a regular file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _verify_effective(repo: Path, edits: dict[Path, dict[str, str]]) -> None:
    for fields in edits.values():
        for key, value in fields.items():
            if _git("config", "--includes", "--get", key, repo=repo) != value:
                raise ValueError("Another configuration source overrides the local identity")


def _apply(
    edits: dict[Path, dict[str, str]], *, yes: bool, effective_repo: Path | None = None
) -> tuple[Path | None, list[str]]:
    """Use Git's writer only after backup, recording every changed target for rollback."""
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
    _safe_path(state_root())
    backup = new_backup([path for path in changed if path.exists()])
    receipt = backup / "git-profile.json"
    record = {
        "application": "git-profile",
        "status": "prepared",
        "files": [
            {"path": str(path), "before": before[path], "after": before[path]} for path in changed
        ],
    }
    atomic_write(receipt, (json.dumps(record, indent=2) + "\n").encode())
    try:
        for path, fields in changed.items():
            if _hash(path) != before[path]:
                raise ValueError(f"Configuration changed since planning: {path}")
            item = next(item for item in record["files"] if item["path"] == str(path))
            if not path.exists():
                path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                atomic_write(path, b"")
                item["after"] = _hash(path)
                atomic_write(receipt, (json.dumps(record, indent=2) + "\n").encode())
            for key, value in fields.items():
                if _hash(path) != item["after"]:
                    raise ValueError(f"Configuration changed during apply: {path}")
                _git("config", "--file", str(path), "--replace-all", "--", key, value)
                item["after"] = _hash(path)
                atomic_write(receipt, (json.dumps(record, indent=2) + "\n").encode())
        for path, fields in changed.items():
            for key, value in fields.items():
                if _git("config", "--file", str(path), "--no-includes", "--get-all", key) != value:
                    raise ValueError(f"Configuration readback failed: {path}")
        if effective_repo is not None:
            _verify_effective(effective_repo, edits)
        record["status"] = "complete"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        record["status"] = "failed"
        raise ValueError(f"Git profile apply failed; inspect private backup {backup}") from None
    finally:
        atomic_write(receipt, (json.dumps(record, indent=2) + "\n").encode())
    return backup, [action.replace("would update", "updated", 1) for action in actions]


def configure_profile(
    name: str, source_repo: Path, directories: list[Path], *, yes: bool = False
) -> tuple[Path | None, list[str]]:
    """Capture one confirmed Git identity and add defaults for reviewed directories."""
    identity = _source_identity(source_repo)
    target = profile_path(name)
    if target.exists() and read_profile(name) != identity:
        raise ValueError("Existing profile differs; preserve it and choose another explicit name")
    config = _safe_path(Path(os.environ.get("GIT_CONFIG_GLOBAL", Path.home() / ".gitconfig")))
    fields = {}
    for directory in directories:
        directory = _safe_path(directory)
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


def restore_profile(backup_id: str, *, yes: bool = False) -> list[tuple[str, str]]:
    """Restore only this operation's files after verifying no later edit is lost."""
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", backup_id):
        raise ValueError("Invalid backup ID")
    folder = _safe_path(state_root() / "backups" / backup_id)
    record = json.loads((folder / "git-profile.json").read_text())
    if record.get("application") != "git-profile" or record.get("status") not in {
        "prepared",
        "complete",
        "failed",
    }:
        raise ValueError("A Git profile operation receipt is required")
    items = record["files"]
    targets = {str(_safe_path(Path(item["path"]))): item for item in items}
    if len(targets) != len(items):
        raise ValueError("Repeated rollback targets")
    for path, item in targets.items():
        if _hash(Path(path)) != item["after"]:
            raise ValueError(f"Rollback blocked by later changes: {path}")
    manifest = json.loads((folder / "manifest.json").read_text())
    expected = {
        path: item["before"] for path, item in targets.items() if item["before"] is not None
    }
    backed_up = {}
    for item in manifest["items"]:
        relative = Path(item["home_relative"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe backup target")
        target = str(_safe_path(Path.home() / relative))
        if target != item["source"]:
            raise ValueError("Backup target paths disagree")
        backed_up[target] = item["sha256"]
    if backed_up != expected or len(backed_up) != len(manifest["items"]):
        raise ValueError("Backup targets do not match the operation receipt")
    preview = restore(backup_id)
    actions = [(path, "would-restore" if state == "conflict" else state) for path, state in preview]
    created = [Path(path) for path, item in targets.items() if item["before"] is None]
    actions += [(str(path), "would-remove-created") for path in created]
    if yes:
        if existing_created := [path for path in created if path.is_file()]:
            new_backup(existing_created)
        actions = restore(backup_id, apply=True, replace_current=True)
        for path in created:
            path.unlink(missing_ok=True)
            actions.append((str(path), "removed-created"))
    return actions
