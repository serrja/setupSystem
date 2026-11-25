"""Exercise repository-scoped identity restoration and effective Git readback."""

import asyncio
import subprocess
from pathlib import Path

import pytest
from setup_plugin_git import Plugin, Settings
from setup_plugin_git.profiles import ImportProfile, UseProfile

from setup_system.application.recovery import restore
from setup_system_sdk import Mutation, Outcome


def test_import_and_use_are_recoverable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "HOME": tmp_path,
        "XDG_CONFIG_HOME": tmp_path / "config",
        "XDG_STATE_HOME": tmp_path / "state",
    }.items():
        monkeypatch.setenv(key, str(value))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    source = tmp_path / "origin"
    target = tmp_path / "target"
    for repo in (source, target):
        subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    for key, value in (("user.name", "Fixture"), ("user.email", "test@example.org")):
        subprocess.run(["git", "-C", str(source), "config", "--local", key, value], check=True)
    original = (target / ".git/config").read_bytes()
    plugin = Plugin()
    ids = []
    for action, selection in (
        ("import-profile", ImportProfile(profile="work", repository=source)),
        ("use-profile", UseProfile(profile="work", repository=target)),
    ):
        plan = asyncio.run(plugin.plan(action, Settings(), selection))
        req = Mutation(
            action_id=action, inputs=selection.model_dump(mode="json"), settings={}, plan=plan
        )
        result = asyncio.run(plugin.apply_request(action, req))
        assert result.outcome == Outcome.CHANGED and result.recovery_id
        ids.append(result.recovery_id)
        assert asyncio.run(plugin.verify(plan, Settings(), selection)).outcome == Outcome.COMPLETE
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "Other")
    assert asyncio.run(plugin.verify(plan, Settings(), selection)).outcome == Outcome.PARTIAL
    for key in reversed(ids):
        restore(key, apply=True)
    assert (target / ".git/config").read_bytes() == original
    assert not (tmp_path / "config/git/identities/work.gitconfig").exists()
