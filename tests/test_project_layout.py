"""Relocation preflight must preserve Git and filesystem state."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from setup_system.application.configuration import apply_configuration
from setup_system.application.project_layout import (
    active_projects,
    apply_migration,
    load_layout,
    plan_migration,
    prepare_owner_directories,
)
from setup_system.planning import Plan, Platform


def _git(path: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(path), *arguments],
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"},
    )


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "source"
    (root / "manifests").mkdir(parents=True)
    (root / "manifests/projects.toml").write_text(
        'schemaVersion = 1\nprojectsRoot = "$HOME/Projects"\n'
        'ownerGroups = ["serrja"]\n'
        '[[project]]\nid = "serrja/example"\nlegacyPath = "$HOME/example"\n'
        'origin = "none"\nstate = "existing"\n'
    )
    return home, root


def test_plan_migration_keeps_dirty_checkout_untouched(tmp_path: Path) -> None:
    home, root = _layout(tmp_path)
    legacy = home / "example"
    legacy.mkdir()
    _git(legacy, "init", "-q", "-b", "main")
    (legacy / "README.md").write_text("original\n")
    _git(legacy, "add", "README.md")
    _git(
        legacy,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "initial",
    )
    (legacy / "README.md").write_text("edited\n")
    (legacy / "staged.txt").write_text("staged\n")
    _git(legacy, "add", "staged.txt")
    (legacy / "untracked.bin").write_bytes(b"\x00\xff")
    index = legacy / ".git/index"
    before_index = index.read_bytes()
    before_files = sorted(str(path.relative_to(home)) for path in home.rglob("*") if path.is_file())

    layout = load_layout(root, home=home)
    move = plan_migration(layout, current_directory=tmp_path)[0]

    assert move.status == "backupRequired"
    assert move.snapshot is not None
    assert (move.snapshot.staged, move.snapshot.unstaged, move.snapshot.untracked) == (1, 1, 1)
    assert index.read_bytes() == before_index
    assert (
        sorted(str(path.relative_to(home)) for path in home.rglob("*") if path.is_file())
        == before_files
    )
    assert not (home / "Projects").exists()


def test_target_only_existing_project_needs_no_legacy_path(tmp_path: Path) -> None:
    home, root = _layout(tmp_path)
    manifest = root / "manifests/projects.toml"
    manifest.write_text(manifest.read_text().replace('legacyPath = "$HOME/example"\n', ""))
    target = home / "Projects/serrja/example"
    target.mkdir(parents=True)
    (target.parent / ".git").mkdir()  # An empty permission marker is not a Git superproject.
    _git(target, "init", "-q", "-b", "main")

    layout = load_layout(root, home=home)

    assert active_projects(layout) == {"serrja/example": target}
    assert plan_migration(layout, current_directory=tmp_path)[0].status == "placed"
    (target.parent / ".git/HEAD").write_text("ref: refs/heads/main\n")
    assert plan_migration(layout, current_directory=tmp_path)[0].status == "blocked"


def test_apply_migration_preserves_git_and_dirty_files(tmp_path: Path) -> None:
    home, root = _layout(tmp_path)
    source = home / "example"
    source.mkdir()
    _git(source, "init", "-q", "-b", "main")
    (source / "README.md").write_text("original\n")
    _git(source, "add", "README.md")
    _git(
        source,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "initial",
    )
    (source / "README.md").write_text("edited\n")
    (source / "staged.txt").write_text("staged\n")
    _git(source, "add", "staged.txt")
    (source / "untracked.bin").write_bytes(b"\x00\xff")
    layout = load_layout(root, home=home)
    target = home / "Projects/serrja/example"
    target.parent.mkdir(parents=True)
    archive = tmp_path / "backup.tar"
    with tarfile.open(archive, "w") as writer:
        writer.add(source, arcname="repo")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    def output(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(source), *args]).decode()

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "repos": [
                    {
                        "root": str(source),
                        "archive": archive.name,
                        "sha256": digest,
                        "head": output("rev-parse", "HEAD").strip(),
                        "branch": "main",
                        "refs": output("show-ref"),
                        "status": output("status", "--porcelain=v1", "--untracked-files=all"),
                        "indexSha256": hashlib.sha256(
                            (source / ".git/index").read_bytes()
                        ).hexdigest(),
                    }
                ],
            }
        )
    )
    before_index = (source / ".git/index").read_bytes()
    result = apply_migration(
        layout,
        "serrja/example",
        backup_manifest=manifest,
        result_directory=tmp_path / "privateResults",
        current_directory=tmp_path,
    )

    assert result.is_file()
    assert json.loads(result.read_text())["status"] == "verified"
    assert not source.exists()
    assert (target / ".git/index").read_bytes() == before_index
    assert (target / "README.md").read_text() == "edited\n"
    assert (target / "untracked.bin").read_bytes() == b"\x00\xff"


def test_plan_migration_reports_unborn_checkout_for_review(tmp_path: Path) -> None:
    home, root = _layout(tmp_path)
    legacy = home / "example"
    legacy.mkdir()
    _git(legacy, "init", "-q", "-b", "main")
    (legacy / "README.md").write_text("local foundation\n")

    move = plan_migration(load_layout(root, home=home), current_directory=tmp_path)[0]

    assert move.status == "review"
    assert move.snapshot is not None
    assert move.snapshot.head == "UNBORN"
    assert move.snapshot.untracked == 1
    assert (legacy / "README.md").read_text() == "local foundation\n"
    assert not (home / "Projects").exists()


def test_destination_collision_blocks_without_merge(tmp_path: Path) -> None:
    home, root = _layout(tmp_path)
    legacy = home / "example"
    legacy.mkdir()
    target = home / "Projects/serrja/example"
    target.mkdir(parents=True)
    (target / "keep.txt").write_text("preserve")

    move = plan_migration(load_layout(root, home=home), current_directory=tmp_path)[0]

    assert move.status == "blocked"
    assert (target / "keep.txt").read_text() == "preserve"


def test_prepare_creates_only_owner_directories(tmp_path: Path) -> None:
    home, root = _layout(tmp_path)
    layout = load_layout(root, home=home)

    paths = prepare_owner_directories(layout)

    assert paths == (home / "Projects", home / "Projects/serrja")
    assert all(path.is_dir() for path in paths)
    assert not (home / "Projects/serrja/example").exists()
    assert prepare_owner_directories(layout) == paths
    (home / "Projects/.git").mkdir()
    with pytest.raises(ValueError, match="Git repository"):
        prepare_owner_directories(layout)


def test_catalog_rejects_traversal_and_duplicate_checkouts(tmp_path: Path) -> None:
    home, root = _layout(tmp_path)
    manifest = root / "manifests/projects.toml"
    manifest.write_text(manifest.read_text().replace("$HOME/example", "$HOME/../outside"))
    with pytest.raises(ValueError, match="unsafe component"):
        load_layout(root, home=home)

    manifest.write_text(manifest.read_text().replace("$HOME/../outside", "$HOME/example"))
    legacy = home / "example"
    target = home / "Projects/serrja/example"
    for path in (legacy, target):
        path.mkdir(parents=True)
        _git(path, "init", "-q", "-b", "main")
    with pytest.raises(ValueError, match="Two active checkouts"):
        active_projects(load_layout(root, home=home))


def test_debian_profile_install_keeps_user_zsh_text_and_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".zshrc").write_text("# personal setting\n")
    history = home / ".zsh_history"
    history.write_bytes(b"old private history\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local/state"))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setattr(
        "setup_system.application.configuration.create_plan",
        lambda *_args: Plan(Platform("debian", "13", "x86_64", True, False, "test"), (), ()),
    )
    monkeypatch.setattr("setup_system.application.configuration.shutil.which", lambda _name: None)
    source_root = Path(__file__).resolve().parents[1]

    apply_configuration(source_root)

    zshrc = (home / ".zshrc").read_text()
    assert zshrc.startswith("# personal setting\n")
    assert zshrc.count("# >>> setupSystem >>>") == 1
    assert (home / ".config/zsh/projects.zsh").is_file()
    assert (home / ".local/state/zsh").is_dir()
    assert history.read_bytes() == b"old private history\n"

    apply_configuration(source_root)
    assert (home / ".zshrc").read_text() == zshrc
