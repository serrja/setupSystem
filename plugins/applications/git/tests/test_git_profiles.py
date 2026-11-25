"""Use the native Git parser and an isolated HOME for profile publication."""

import asyncio
import subprocess
from pathlib import Path

import pytest
from setup_plugin_git import Account, Plugin, Settings
from setup_plugin_git.profiles import Profile

from setup_system_sdk import Outcome
from setup_system_sdk.native import ActionRequest


def test_conditional_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    repo = home / "work" / "café checkout"
    repo.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for key in ("XDG_STATE_HOME", "XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL"):
        monkeypatch.delenv(key, raising=False)
    subprocess.run(["/usr/bin/git", "init", "--quiet", str(repo)], check=True)
    original = "[alias]\n\tkeep = status\n"
    (home / ".gitconfig").write_text(original)
    profile = Profile(
        profile="work", directory=repo.parent, name='Мария "$HOME"', email="test@example.org"
    )
    plugin = Plugin()
    settings = Settings()
    plan = asyncio.run(plugin.plan("configure", settings, profile))
    assert plan.outcome == Outcome.READY
    assert (home / ".gitconfig").read_text() == original
    request = ActionRequest(
        action_id="configure", settings={}, inputs=profile.model_dump(mode="json"), plan=plan
    )
    applied = asyncio.run(plugin.apply_request("configure", request))
    assert applied.outcome == Outcome.CHANGED
    verified = asyncio.run(plugin.verify(plan, settings, profile))
    assert verified.outcome == Outcome.COMPLETE
    assert original in (home / ".gitconfig").read_text()
    assert (
        subprocess.check_output(
            ["/usr/bin/git", "-C", str(repo), "config", "user.name"], text=True
        ).strip()
        == profile.name
    )
    assert asyncio.run(plugin.plan("configure", settings, profile)).outcome == Outcome.NO_CHANGES
    assert asyncio.run(plugin.apply_request("configure", request)).outcome == Outcome.BLOCKED


def test_reject_changed_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    inputs = Profile(profile="my", directory=tmp_path, name="Author", email="a@example.org")
    plugin = Plugin()
    plan = asyncio.run(plugin.plan("configure", Settings(), inputs))
    config = tmp_path / ".gitconfig"
    config.write_text("[alias]\n\tmine = status\n")
    result = asyncio.run(
        plugin.apply_request(
            "configure",
            ActionRequest(
                action_id="configure", settings={}, inputs=inputs.model_dump(mode="json"), plan=plan
            ),
        )
    )
    assert result.outcome == Outcome.BLOCKED
    assert "mine" in config.read_text()
    assert not (tmp_path / ".config").exists()


def test_account_host_is_approved() -> None:
    plan = Plugin().prepare("login", Settings(), Account(host="github.com.evil.test"))
    assert plan.outcome == Outcome.BLOCKED
    approved = Settings(enterprise_host="code.example.org")
    plan = Plugin().prepare("login", approved, Account(host="code.example.org"))
    assert plan.outcome in (Outcome.NEEDS_USER_ACTION, Outcome.BLOCKED)
