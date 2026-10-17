"""Regression checks for scoped shell and RTK changes."""

from __future__ import annotations

from pathlib import Path

import pytest

from setup_system.application import rtk, shell


def _shell_fixture(home: Path, root: Path) -> shell.ShellStatus:
    assets = root / "assets/zsh"
    assets.mkdir(parents=True)
    (assets / "init.zsh").write_text("typeset -g TEST_INIT=1\n")
    (assets / "environment.zsh").write_text("typeset -g TEST_ENV=1\n")
    (home / ".zshrc").write_text("# user setting\n")
    return shell.ShellStatus("test", home, home, Path("/bin/bash"), Path("/bin/sh"), False)


def test_shell_plan_is_read_only_and_restore_protects_later_edits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "source"
    status = _shell_fixture(home, root)
    monkeypatch.setattr(Path, "home", lambda: home)
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
    with pytest.raises(ValueError, match="User changed file"):
        shell.restore_shell(backup.name)
    assert (home / ".zshrc").read_text().endswith("# later edit\n")

    (home / ".zshrc").write_text(managed)
    restored = shell.restore_shell(backup.name)
    assert len(restored) == len(applied)
    assert (home / ".zshrc").read_text() == "# user setting\n"
    assert not (home / ".zprofile").exists()


def test_shell_rejects_external_state_before_writing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "source"
    status = _shell_fixture(home, root)
    monkeypatch.setattr(shell, "inspect_shell", lambda: status)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "elsewhere"))
    monkeypatch.setattr(shell, "validate_shell", lambda *_args: pytest.fail("startup ran"))
    with pytest.raises(ValueError, match="XDG_STATE_HOME"):
        shell.apply_shell(root)
    assert not (home / ".config").exists()


def test_rtk_detects_product_and_preserves_pinned_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / "rtk"
    binary.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "rtk 0.48.0"; '
        'else echo "Show token savings summary"; fi\n'
    )
    binary.chmod(0o755)
    root = tmp_path / "source"
    manifest = root / "manifests"
    manifest.mkdir(parents=True)
    (manifest / "rtk.toml").write_text('product = "rtk-ai/rtk"\nversion = "0.48.0"\n')
    monkeypatch.setenv("PATH", str(tmp_path))

    status, action = rtk.plan_rtk(root)
    assert status.product_matches and status.version == "0.48.0"
    assert action.startswith("unchanged:")
    assert rtk.install_rtk(root) == (action, None)
    assert not (tmp_path / "source" / "backups").exists()


def test_rtk_blocks_same_named_other_product(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / "rtk"
    binary.write_text('#!/bin/sh\necho "rtk 0.48.0"\n')
    binary.chmod(0o755)
    root = tmp_path / "source"
    manifest = root / "manifests"
    manifest.mkdir(parents=True)
    (manifest / "rtk.toml").write_text('product = "rtk-ai/rtk"\nversion = "0.48.0"\n')
    monkeypatch.setenv("PATH", str(tmp_path))

    _, action = rtk.plan_rtk(root)
    assert action.startswith("blocked:")
    with pytest.raises(ValueError, match="not rtk-ai/rtk"):
        rtk.install_rtk(root)
