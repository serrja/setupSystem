"""Regression checks for scoped shell and RTK changes."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from setup_plugin_zsh import runtime as shell
from setup_plugin_zsh.facts import ShellStatus

from setup_system.application.recovery import restore


def _shell_fixture(home: Path, root: Path) -> ShellStatus:
    assets = root / "assets"
    assets.mkdir(parents=True)
    (assets / "init.zsh").write_text("typeset -g TEST_INIT=1\n")
    (assets / "environment.zsh").write_text("typeset -g TEST_ENV=1\n")
    (home / ".zshrc").write_text("# user setting\n")
    return ShellStatus(
        user="test",
        home=home,
        startup_dir=home,
        login_shell=Path("/bin/bash"),
        zsh=Path("/bin/sh"),
        registered=False,
    )


def test_shell_plan_is_read_only_and_restore_protects_later_edits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    root = tmp_path / "source"
    status = _shell_fixture(home, root)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(shell, "inspect_shell", lambda: status)
    monkeypatch.setattr(shell, "validate_shell", lambda *_args: None)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local/state"))

    _, planned = shell.plan_shell(root)
    assert planned and all(action == "change" for _, action in planned)
    assert not (home / ".config").exists()
    assert (home / ".zshrc").read_text() == "# user setting\n"

    backup, applied, _ = shell.apply_shell(root)
    assert backup.is_dir()
    assert all(action == "changed" for _, action in applied)
    assert "# user setting" in (home / ".zshrc").read_text()
    _, planned = shell.plan_shell(root)
    assert all(action == "unchanged" for _, action in planned)

    managed = (home / ".zshrc").read_text()
    (home / ".zshrc").write_text(managed + "# later edit\n")
    with pytest.raises(ValueError, match="later edit"):
        restore(backup.name, apply=True)
    assert (home / ".zshrc").read_text().endswith("# later edit\n")

    (home / ".zshrc").write_text(managed)
    restored = restore(backup.name, apply=True)
    assert len(restored) == len(applied)
    assert (home / ".zshrc").read_text() == "# user setting\n"
    assert not (home / ".zprofile").exists()


def test_shell_rejects_external_state_before_writing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    root = tmp_path / "source"
    status = _shell_fixture(home, root)
    monkeypatch.setattr(shell, "inspect_shell", lambda: status)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "elsewhere"))
    monkeypatch.setattr(shell, "validate_shell", lambda *_args: pytest.fail("startup ran"))
    with pytest.raises(ValueError, match="XDG_STATE_HOME"):
        shell.apply_shell(root)
    assert not (home / ".config").exists()


def test_shipped_zsh_startup_loads_textual_launcher_and_completion() -> None:
    zsh = shutil.which("zsh")
    if zsh is None:
        pytest.skip("Native interactive startup requires the optional Zsh executable")
    shell.validate_shell(Path(shell.__file__).parent, Path(zsh))


def test_selected_shell_renders_release_palette_and_preserves_edited_prompt(
    tmp_path, monkeypatch
) -> None:
    from setup_system.infrastructure.managed_files import apply_files
    from setup_system.infrastructure.palette import palette

    home = tmp_path / "prompt home"
    home.mkdir(mode=0o700)
    root = tmp_path / "release"
    status = _shell_fixture(home, root)
    shipped = Path(shell.__file__).parent
    prompt = "assets/prompt.zsh"
    (root / prompt).write_bytes((shipped / prompt).read_bytes())
    colors = "config/shared/palette.toml"
    selected = root / colors
    selected.parent.mkdir(parents=True)
    from setup_system.layout import resource_root

    resource = resource_root()
    selected.write_text(
        (resource / colors).read_text().replace(palette(resource)["green"], "#123456")
    )
    monkeypatch.setattr(shell, "resource_root", lambda: root)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "state"))
    monkeypatch.setattr(shell, "inspect_shell", lambda: status)
    monkeypatch.setattr(shell, "validate_shell", lambda *_args: None)
    installed = home / "config/zsh/prompt.zsh"
    old = b"# managed by an older release\n"
    apply_files({installed: old})
    installed.write_bytes(old + b"# personal customization\n")
    with pytest.raises(ValueError, match="edited"):
        shell.plan_shell(root)
    with pytest.raises(ValueError, match="edited"):
        shell.apply_shell(root)
    assert installed.read_bytes().endswith(b"# personal customization\n")

    installed.write_bytes(old)
    operation, _, _ = shell.apply_shell(root)
    assert b"%F{#123456}" in installed.read_bytes()
    assert b"@{" not in installed.read_bytes()
    restore(operation.name, apply=True)
    assert installed.read_bytes() == old
