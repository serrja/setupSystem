"""Vaults and note contents stay outside application cleanup."""

import asyncio
from pathlib import Path

import pytest
from setup_plugin_obsidian import NewVault, Plugin, Settings, Theme

from setup_system_sdk import Mutation, Outcome


def test_vault_creation_preserves_user_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / "Notes"
    plugin = Plugin()
    inputs = NewVault(path=target)
    plan = plugin.prepare("create-vault", Settings(), inputs)
    assert not target.exists() and plan.resources[0].cleanup is False
    result = asyncio.run(
        plugin.apply_request(
            "create-vault",
            Mutation(
                action_id="create-vault",
                inputs=inputs.model_dump(mode="json"),
                settings={},
                plan=plan,
            ),
        )
    )
    assert result.outcome == Outcome.CHANGED and target.is_dir()
    (target / "private.md").write_text("user note")
    assert asyncio.run(plugin.verify(plan, Settings(), inputs)).outcome == Outcome.COMPLETE
    assert plugin.prepare("create-vault", Settings(), inputs).outcome == Outcome.BLOCKED
    assert (target / "private.md").read_text() == "user note"
    (target / ".obsidian").mkdir()
    with pytest.raises(ValueError, match="Nested"):
        plugin.prepare("create-vault", Settings(), NewVault(path=target / "nested"))


def test_theme_requires_open_matching_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / "vault"
    target.mkdir()
    monkeypatch.setattr("setup_plugin_obsidian.running", lambda selected: False)
    assert (
        Plugin().prepare("set-theme", Settings(vault=target), Theme(name="Default")).outcome
        == Outcome.NEEDS_USER_ACTION
    )
