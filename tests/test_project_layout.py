"""Relocation preflight must preserve Git and filesystem state."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from setup_system.application.configuration import applyConfiguration
from setup_system.application.projectLayout import (
    activeProjects,
    loadLayout,
    planMigration,
    prepareOwnerDirectories,
)
from setup_system.planning import Plan, Platform


def _git(path: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(path), *arguments],
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"},
    )


def _layout(tmpPath: Path) -> tuple[Path, Path]:
    home = tmpPath / "home"
    home.mkdir()
    root = tmpPath / "source"
    (root / "manifests").mkdir(parents=True)
    (root / "manifests/projects.toml").write_text(
        'schemaVersion = 1\nprojectsRoot = "$HOME/Projects"\n'
        'ownerGroups = ["serrja"]\n'
        '[[project]]\nid = "serrja/example"\nlegacyPath = "$HOME/example"\n'
        'origin = "none"\nstate = "existing"\n'
    )
    return home, root


def test_planMigrationKeepsDirtyCheckoutUntouched(tmp_path: Path) -> None:
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
    beforeIndex = index.read_bytes()
    beforeFiles = sorted(str(path.relative_to(home)) for path in home.rglob("*") if path.is_file())

    layout = loadLayout(root, home=home)
    move = planMigration(layout, currentDirectory=tmp_path)[0]

    assert move.status == "backupRequired"
    assert move.snapshot is not None
    assert (move.snapshot.staged, move.snapshot.unstaged, move.snapshot.untracked) == (1, 1, 1)
    assert index.read_bytes() == beforeIndex
    assert (
        sorted(str(path.relative_to(home)) for path in home.rglob("*") if path.is_file())
        == beforeFiles
    )
    assert not (home / "Projects").exists()


def test_planMigrationReportsUnbornCheckoutForReview(tmp_path: Path) -> None:
    home, root = _layout(tmp_path)
    legacy = home / "example"
    legacy.mkdir()
    _git(legacy, "init", "-q", "-b", "main")
    (legacy / "README.md").write_text("local foundation\n")

    move = planMigration(loadLayout(root, home=home), currentDirectory=tmp_path)[0]

    assert move.status == "review"
    assert move.snapshot is not None
    assert move.snapshot.head == "UNBORN"
    assert move.snapshot.untracked == 1
    assert (legacy / "README.md").read_text() == "local foundation\n"
    assert not (home / "Projects").exists()


def test_destinationCollisionBlocksWithoutMerge(tmp_path: Path) -> None:
    home, root = _layout(tmp_path)
    legacy = home / "example"
    legacy.mkdir()
    target = home / "Projects/serrja/example"
    target.mkdir(parents=True)
    (target / "keep.txt").write_text("preserve")

    move = planMigration(loadLayout(root, home=home), currentDirectory=tmp_path)[0]

    assert move.status == "blocked"
    assert (target / "keep.txt").read_text() == "preserve"


def test_prepareCreatesOnlyOwnerDirectories(tmp_path: Path) -> None:
    home, root = _layout(tmp_path)
    layout = loadLayout(root, home=home)

    paths = prepareOwnerDirectories(layout)

    assert paths == (home / "Projects", home / "Projects/serrja")
    assert all(path.is_dir() for path in paths)
    assert not (home / "Projects/serrja/example").exists()
    assert prepareOwnerDirectories(layout) == paths
    (home / "Projects/.git").mkdir()
    with pytest.raises(ValueError, match="Git repository"):
        prepareOwnerDirectories(layout)


def test_catalogRejectsTraversalAndDuplicateCheckouts(tmp_path: Path) -> None:
    home, root = _layout(tmp_path)
    manifest = root / "manifests/projects.toml"
    manifest.write_text(manifest.read_text().replace("$HOME/example", "$HOME/../outside"))
    with pytest.raises(ValueError, match="unsafe component"):
        loadLayout(root, home=home)

    manifest.write_text(manifest.read_text().replace("$HOME/../outside", "$HOME/example"))
    legacy = home / "example"
    target = home / "Projects/serrja/example"
    for path in (legacy, target):
        path.mkdir(parents=True)
        _git(path, "init", "-q", "-b", "main")
    with pytest.raises(ValueError, match="Two active checkouts"):
        activeProjects(loadLayout(root, home=home))


def test_debianProfileInstallKeepsUserZshTextAndHistory(
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
        "setup_system.application.configuration.createPlan",
        lambda *_args: Plan(Platform("debian", "13", "x86_64", True, False, "test"), (), ()),
    )
    monkeypatch.setattr("setup_system.application.configuration.shutil.which", lambda _name: None)
    sourceRoot = Path(__file__).resolve().parents[1]

    applyConfiguration(sourceRoot)

    zshrc = (home / ".zshrc").read_text()
    assert zshrc.startswith("# personal setting\n")
    assert zshrc.count("# >>> setupSystem >>>") == 1
    assert (home / ".config/zsh/projects.zsh").is_file()
    assert (home / ".local/state/zsh").is_dir()
    assert history.read_bytes() == b"old private history\n"

    applyConfiguration(sourceRoot)
    assert (home / ".zshrc").read_text() == zshrc
