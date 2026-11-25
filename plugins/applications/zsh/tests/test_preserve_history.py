"""Keep personal Zsh startup text and history."""

from pathlib import Path

import pytest
from setup_plugin_zsh import runtime as shell
from setup_plugin_zsh.facts import ShellStatus


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
    source = Path(shell.__file__).parent
    targets = shell._targets(source, status)
    assert targets[home / ".zshrc"].startswith(b"# personal setting\n")
    assert targets[home / ".zshrc"].count(b"# >>> setupSystem >>>") == 1
    assert home / ".config/zsh/projects.zsh" in targets
    assert history not in targets and history.read_bytes() == b"old private history\n"
