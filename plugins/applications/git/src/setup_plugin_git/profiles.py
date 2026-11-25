"""Prepare native Git configuration fragments and publish the confirmed bytes."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator

from setup_system.domain.receipts import FileState
from setup_system.infrastructure.file_publication import observe
from setup_system.infrastructure.managed_files import apply_files
from setup_system.infrastructure.processes import run_command
from setup_system.infrastructure.user_storage import checked_directory, inside_home
from setup_system_sdk import Inputs, Resource


class NamedProfile(Inputs):
    profile: str = Field(
        min_length=1, max_length=64, pattern="^[a-z][a-z0-9_-]*$", title="Profile name"
    )


class RepositoryProfile(NamedProfile):
    repository: Path = Field(title="Checkout root", json_schema_extra={"ui": {"widget": "path"}})


class ImportProfile(RepositoryProfile):
    """Save the selected repository identity in a private fragment."""


class UseProfile(RepositoryProfile):
    """Override identity only inside this exact repository."""


class Profile(NamedProfile):
    directory: Path = Field(
        title="Repository directory",
        description="Apply this identity to repositories under this existing directory.",
        json_schema_extra={"ui": {"widget": "path"}},
    )
    name: Annotated[str, Field(min_length=1, max_length=128, title="Commit author")]
    email: str = Field(max_length=254, pattern=r"^[^@\s<>]+@[^@\s<>]+$", title="Commit email")
    editor: Literal["nvim", "vim", "nano"] = Field(default="nvim", title="Commit editor")
    signing_key: str = Field(
        default="",
        pattern="^([0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})?$",
        description="Full OpenPGP fingerprint. Leave empty for unsigned commits.",
    )

    @field_validator("name", "email")
    @classmethod
    def printable_identity(cls, value: str) -> str:
        if not value.strip() or not value.isprintable():
            raise ValueError("Enter a printable single-line identity")
        return value


def git_config(path: Path, *args: str, missing: bool = False) -> str:
    result = run_command(
        ["/usr/bin/git", "config", "--file", str(path), "--no-includes", *args],
        timeout=10,
        check=False,
        discard_stderr=True,
        max_output_bytes=8192,
        stdin=subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home())},
    )
    if result.returncode and not (missing and result.returncode == 1):
        raise ValueError("Native Git configuration validation failed")
    return result.stdout.removesuffix("\n")


@dataclass(frozen=True)
class Edit:
    path: Path
    state: FileState | None
    content: bytes

    def resource(self) -> Resource:
        return Resource(
            kind="file",
            identity=str(self.path),
            ownership="created" if self.state is None else "modified",
            before=self.state.sha256 if self.state else None,
            after=hashlib.sha256(self.content).hexdigest(),
            cleanup=self.state is None,
            permissions=self.state.mode if self.state else 0o600,
        )


class Style(Inputs):
    """Install the optional Git color and alias fragment."""


def style_changes() -> list[Edit]:
    config = inside_home(Path.home() / ".gitconfig")
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    fragment = inside_home(root / "git/setup-system-style.inc")
    content = (Path(__file__).parent / "style.inc").read_bytes()
    before = observe(fragment)
    if before and before.sha256 != hashlib.sha256(content).hexdigest():
        raise ValueError("Review the existing Git style fragment")
    state = observe(config)
    original = config.read_bytes() if state else b""
    if len(original) > 1024 * 1024 or (
        state and hashlib.sha256(original).hexdigest() != state.sha256
    ):
        raise ValueError("Global Git configuration changed or exceeds the size limit")
    with tempfile.TemporaryDirectory(prefix="git-style-") as stage:
        file = Path(stage) / "global"
        file.write_bytes(original)
        if (
            str(fragment)
            not in git_config(file, "--get-all", "include.path", missing=True).splitlines()
        ):
            git_config(file, "--add", "include.path", str(fragment))
        return [Edit(fragment, before, content), Edit(config, state, file.read_bytes())]


def configure(profile: Profile, *, attach: bool = True) -> list[Edit]:
    directory = checked_directory(inside_home(profile.directory))
    if not directory.is_dir() or any(ch in str(directory) for ch in "*?[]\\\n\r"):
        raise ValueError("Choose an existing literal directory inside HOME")
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    fragment = inside_home(root / "git" / "identities" / f"{profile.profile}.gitconfig")
    global_file = inside_home(Path.home() / ".gitconfig")
    include = f"includeIf.gitdir:{directory}/.path"
    fields = {
        "user.name": profile.name,
        "user.email": profile.email,
        "user.useConfigOnly": "true",
        "core.editor": profile.editor,
        "commit.gpgSign": "true" if profile.signing_key else "false",
    }
    if profile.signing_key:
        fields["user.signingKey"] = profile.signing_key
        fields["gpg.format"] = "openpgp"
    changes_by_path = [(fragment, fields)]
    if attach:
        changes_by_path.append((global_file, {include: str(fragment)}))
    edits = []
    with tempfile.TemporaryDirectory(prefix="setup-system-git-profile-") as temp:
        for index, (path, changes) in enumerate(changes_by_path):
            checked_directory(path.parent)
            if path.exists() and path.stat().st_size > 1024 * 1024:
                raise ValueError("Git configuration exceeds the inspection limit")
            state = observe(path)
            staged = Path(temp) / str(index)
            original = path.read_bytes() if state else b""
            if state and hashlib.sha256(original).hexdigest() != state.sha256:
                raise ValueError("Git configuration changed during inspection")
            staged.write_bytes(original)
            if path == global_file:
                existing = git_config(staged, "--get-all", "--", include, missing=True)
                if existing and existing != str(fragment):
                    raise ValueError("A different include already manages this directory")
            elif original:
                allowed = {key.lower() for key in fields} | {"user.signingkey", "gpg.format"}
                keys = git_config(staged, "--name-only", "--list").splitlines()
                if any(key.lower() not in allowed for key in keys):
                    raise ValueError("This profile contains additional user settings")
                if not profile.signing_key:
                    for key in ("user.signingKey", "gpg.format"):
                        if git_config(staged, "--get-all", key, missing=True):
                            git_config(staged, "--unset-all", key)
            for key, value in changes.items():
                if git_config(staged, "--get-all", "--", key, missing=True) != value:
                    git_config(staged, "--replace-all", "--", key, value)
                if git_config(staged, "--get-all", "--", key) != value:
                    raise ValueError("Staged Git configuration failed readback")
            edits.append(Edit(path, state, staged.read_bytes()))
    return edits


def repository(path: Path) -> Path:
    path = checked_directory(inside_home(path))
    if not (path / ".git").is_dir() or (path / ".git").is_symlink():
        raise ValueError("Select an ordinary checkout with its own Git directory")
    rc, top = repo_git(path, "rev-parse", "--show-toplevel")
    if rc or top != str(path):
        raise ValueError("Select the exact checkout root")
    return path


def repo_git(repo: Path, *args: str) -> tuple[int, str]:
    response = run_command(
        ["/usr/bin/git", "-C", str(repo), *args],
        timeout=10,
        discard_stderr=True,
        check=False,
        stdin=subprocess.DEVNULL,
        max_output_bytes=8192,
    )
    return response.returncode, response.stdout.removesuffix("\n")


def author_fields(inputs: UseProfile) -> dict[str, str]:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = inside_home(root / "git" / "identities" / f"{inputs.profile}.gitconfig")
    if observe(path) is None:
        raise ValueError("Select an existing identity fragment")
    names = git_config(path, "--name-only", "--list").splitlines()
    allowed = {
        "user.name",
        "user.email",
        "user.useconfigonly",
        "user.signingkey",
        "commit.gpgsign",
        "gpg.format",
        "core.editor",
    }
    if not set(names) <= allowed:
        raise ValueError("The identity fragment contains unrelated settings")
    fields = {key: git_config(path, "--get-all", key) for key in names}
    if (
        not fields.get("user.name")
        or not fields.get("user.email")
        or any("\n" in v for v in fields.values())
    ):
        raise ValueError("Invalid or repeated profile fields")
    if fields.get("core.editor", "nvim") not in {"nvim", "vim", "nano"}:
        raise ValueError("The profile selects an unsupported editor")
    return fields


def prepare(selection: Profile | ImportProfile | UseProfile | Style) -> list[Edit]:
    if isinstance(selection, Style):
        return style_changes()
    if isinstance(selection, Profile):
        return configure(selection)
    repo = repository(selection.repository)
    if isinstance(selection, ImportProfile):
        fields = {}
        for key in ("user.name", "user.email", "user.signingKey"):
            rc, value = repo_git(repo, "config", "--includes", "--get", key)
            if rc and (rc != 1 or key != "user.signingKey"):
                raise ValueError("Source repository has no complete author identity")
            fields[key] = value
        profile = Profile(
            profile=selection.profile,
            directory=repo,
            name=fields["user.name"],
            email=fields["user.email"],
            signing_key=fields["user.signingKey"],
        )
        edits = configure(profile, attach=False)
        if any(e.state and e.state.sha256 != e.resource().after for e in edits):
            raise ValueError("Choose a new profile name or explicitly edit the existing one")
        return edits
    cfg = repo / ".git/config"
    state = observe(cfg)
    if not state or cfg.stat().st_size > 1024 * 1024:
        raise ValueError("Repository configuration unavailable or oversized")
    if repo_git(repo, "config", "--bool", "--get", "extensions.worktreeConfig")[1] == "true":
        raise ValueError("Per-worktree identities need a separate reviewed operation")
    with tempfile.TemporaryDirectory(prefix="setup-git-repository-") as temp:
        staged = Path(temp) / "config"
        original = cfg.read_bytes()
        if hashlib.sha256(original).hexdigest() != state.sha256:
            raise ValueError("Repository configuration changed")
        staged.write_bytes(original)
        for key, value in author_fields(selection).items():
            if git_config(staged, "--get-all", "--", key, missing=True) != value:
                git_config(staged, "--replace-all", "--", key, value)
        return [Edit(cfg, state, staged.read_bytes())]


def verified_identity(selection: UseProfile) -> bool:
    repo = repository(selection.repository)
    return all(
        repo_git(repo, "config", "--includes", "--get", key) == (0, value)
        for key, value in author_fields(selection).items()
    )


def publish(edits: list[Edit]) -> Path:
    return apply_files(
        {edit.path: edit.content for edit in edits},
        expected={edit.path: edit.state for edit in edits},
        modes={edit.path: 0o600 for edit in edits if edit.state is None},
    )
