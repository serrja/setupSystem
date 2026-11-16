"""Relocation preflight must preserve Git and filesystem state."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from setup_system.application.configuration import apply_configuration
from setup_system.application.project_layout import (
    load_layout,
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
        '[[project]]\nid = "serrja/example"\n'
        'origin = "none"\nstate = "existing"\n'
    )
    return home, root


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
