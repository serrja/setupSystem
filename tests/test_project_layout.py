"""Canonical owner preparation preserves existing user configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from setup_system.domain.workspace_facts import ShellStatus
from setup_system.infrastructure import shell
from setup_system.infrastructure.project_layout import (
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


def test_debian_profile_install_keeps_user_zsh_text_and_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    (home / ".zshrc").write_text("# personal setting\n")
    history = home / ".zsh_history"
    history.write_bytes(b"old private history\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local/state"))
    status = ShellStatus(
        user="fixture",
        home=home,
        startup_dir=home,
        login_shell=Path("/bin/bash"),
        zsh=Path("/bin/zsh"),
        registered=True,
    )
    source = Path(__file__).resolve().parents[1]
    targets = shell._targets(source, status)
    assert targets[home / ".zshrc"].startswith(b"# personal setting\n")
    assert targets[home / ".zshrc"].count(b"# >>> setupSystem >>>") == 1
    assert home / ".config/zsh/projects.zsh" in targets
    assert history not in targets and history.read_bytes() == b"old private history\n"
