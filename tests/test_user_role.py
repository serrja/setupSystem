"""Run the real local Ansible role only against a synthetic HOME/XDG tree."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from setup_system.infrastructure.user_files import apply_files


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "user home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / "cache"))
    return home


def test_real_role_writes_and_repeats_without_another_backup(home: Path) -> None:
    target = home / "config/tool/settings.ini"
    first = apply_files({target: b"color=green\n"})
    assert target.read_bytes() == b"color=green\n"
    assert (first / "operation.json").is_file()
    backups = list((home / "state/setup-system/backups").iterdir())
    apply_files({target: b"color=green\n"})
    assert list((home / "state/setup-system/backups").iterdir()) == backups
    target.write_text("owner change\n")
    with pytest.raises(ValueError, match="edited"):
        apply_files({target: b"color=teal\n"})
    assert target.read_text() == "owner change\n"


def test_partial_runner_failure_records_only_completed_files_and_can_retry(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ansible_runner

    first = home / "config/first"
    second = home / "config/second"

    def partial(**kwargs):
        item = kwargs["extravars"]["user_files_managed_files"][0]
        Path(item["destination"]).write_bytes(Path(item["source"]).read_bytes())
        return SimpleNamespace(rc=1)

    original = ansible_runner.run
    monkeypatch.setattr(ansible_runner, "run", partial)
    with pytest.raises(RuntimeError, match="partially"):
        apply_files({first: b"first", second: b"second"})
    receipt = json.loads((home / "state/setup-system/workstation.json").read_text())
    assert str(first) in receipt and str(second) not in receipt
    monkeypatch.setattr(ansible_runner, "run", original)
    apply_files({first: b"first", second: b"second"})
    assert first.read_bytes() == b"first" and second.read_bytes() == b"second"


def test_user_role_never_follows_symlink_target(home: Path) -> None:
    outside = home / "outside"
    outside.mkdir()
    target = home / "config"
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        apply_files({target / "managed": b"forbidden"})
    assert not list(outside.iterdir())
