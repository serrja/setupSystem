"""Canonical owner preparation preserves existing user configuration."""

from __future__ import annotations

from pathlib import Path

import pytest
from setup_plugin_projects.project_layout import (
    load_layout,
    prepare_owner_directories,
)


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    root = tmp_path / "source"
    (root / "config/workspace").mkdir(parents=True)
    (root / "config/workspace/projects.toml").write_text(
        'schemaVersion = 1\nprojectsRoot = "$HOME/Projects"\n'
        'ownerGroups = ["serrja"]\n'
        '[[project]]\nid = "serrja/example"\n'
        'origin = "https://github.com/serrja/example.git"\n'
    )
    return home, root


def test_prepare_creates_only_owner_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, root = _layout(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local/state"))
    layout = load_layout(root, home=home)

    paths = prepare_owner_directories(layout)

    assert paths == (home / "Projects", home / "Projects/serrja")
    assert all(path.is_dir() for path in paths)
    assert not (home / "Projects/serrja/example").exists()
    assert prepare_owner_directories(layout) == paths
    (home / "Projects/.git").mkdir()
    with pytest.raises(ValueError, match="Git repository"):
        prepare_owner_directories(layout)
