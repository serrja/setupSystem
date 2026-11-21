"""Regression checks for scoped shell and RTK changes."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from setup_system.composition import restore
from setup_system.domain.tool_releases import RtkRelease
from setup_system.domain.workspace_facts import ShellStatus
from setup_system.infrastructure import rtk, shell
from setup_system.infrastructure.catalog_repository import read_contract
from setup_system.resource_locator import ResourceLocator


def _shell_fixture(home: Path, root: Path) -> ShellStatus:
    assets = root / "assets/zsh"
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
    home.mkdir()
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


def test_shipped_zsh_startup_loads_textual_launcher_and_completion() -> None:
    zsh = shutil.which("zsh")
    if zsh is None:
        pytest.skip("Native interactive startup requires the optional Zsh executable")
    shell.validate_shell(Path(__file__).resolve().parents[1], Path(zsh))


@pytest.fixture
def pinned_rtk(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "source"
    release = ResourceLocator.for_root().manifest("rtk.toml")
    target = ResourceLocator.for_root(root).manifest("rtk.toml")
    target.parent.mkdir(parents=True)
    target.write_bytes(release.read_bytes())
    return root, read_contract(target, RtkRelease).version


def test_rtk_detects_product_and_preserves_pinned_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pinned_rtk: tuple[Path, str]
) -> None:
    root, version = pinned_rtk
    binary = tmp_path / "rtk"
    binary.write_text(
        f'#!/bin/sh\nif [ "$1" = "--version" ]; then echo "rtk {version}"; '
        'else echo "Show token savings summary"; fi\n'
    )
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    status, action = rtk.plan_rtk(root)
    assert status.product_matches and status.version == version
    assert action.startswith("unchanged:")
    assert rtk.install_rtk(root) == (action, None)
    assert not (tmp_path / "source" / "backups").exists()


def test_rtk_blocks_same_named_other_product(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pinned_rtk: tuple[Path, str]
) -> None:
    root, version = pinned_rtk
    binary = tmp_path / "rtk"
    binary.write_text(f'#!/bin/sh\necho "rtk {version}"\n')
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    _, action = rtk.plan_rtk(root)
    assert action.startswith("blocked:")
    with pytest.raises(ValueError, match="not rtk-ai/rtk"):
        rtk.install_rtk(root)


def test_git_profile_uses_native_writer_and_shared_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from setup_system.infrastructure import git_profiles

    home = tmp_path / "user home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "state"))
    source, project = home / "source", home / "project"
    for path in (source, project):
        subprocess.run(["git", "init", "-q", str(path)], check=True)
    for key, value in (("user.name", "Fixture"), ("user.email", "fixture@example.invalid")):
        subprocess.run(["git", "-C", str(source), "config", "--local", key, value], check=True)
    template, _ = git_profiles.configure_profile("fixture", source, [], yes=True)
    assert template is not None
    config = project / ".git/config"
    original = config.read_bytes()
    applied, _ = git_profiles.use_profile("fixture", project, yes=True)
    assert applied is not None
    observed = subprocess.check_output(
        ["git", "-C", str(project), "config", "user.email"], text=True
    )
    assert observed.strip() == "fixture@example.invalid"
    assert git_profiles.use_profile("fixture", project, yes=True)[0] is None
    restore(applied.name, apply=True)
    assert config.read_bytes() == original
    restore(template.name, apply=True)
    assert not git_profiles.profile_path("fixture").exists()


def test_git_override_after_publish_reports_recoverable_partial(tmp_path, monkeypatch) -> None:
    import subprocess

    from setup_system.diagnostics import PartialApplyError
    from setup_system.infrastructure import git_profiles
    from setup_system.infrastructure.receipt_repository import list_operations

    home = tmp_path / "git home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "state"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repository = home / "repo"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    profile = git_profiles.profile_path("work")
    profile.parent.mkdir(parents=True)
    profile.write_text("[user]\nname = Author\nemail = fixture@example.invalid\n")
    original = (repository / ".git/config").read_bytes()
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "Different effective author")
    with pytest.raises(PartialApplyError, match="recovery"):
        git_profiles.use_profile("work", repository, yes=True)
    assert (repository / ".git/config").read_bytes() != original
    [(operation, _)] = list_operations()
    restore(operation, apply=True)
    assert (repository / ".git/config").read_bytes() == original


def test_selected_shell_renders_release_palette_and_preserves_edited_prompt(
    tmp_path, monkeypatch
) -> None:
    from setup_system.infrastructure.managed_files import apply_files
    from setup_system.infrastructure.palette import palette

    home = tmp_path / "prompt home"
    home.mkdir()
    root = tmp_path / "release"
    status = _shell_fixture(home, root)
    shipped = ResourceLocator.for_root()
    prompt = "zsh/prompt.zsh"
    ResourceLocator.for_root(root).asset(prompt).write_bytes(shipped.asset(prompt).read_bytes())
    colors = "themes/catppuccin_mocha_green.toml"
    selected = ResourceLocator.for_root(root).resource(colors)
    selected.parent.mkdir(parents=True)
    selected.write_text(shipped.resource(colors).read_text().replace(palette()["green"], "#123456"))
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
