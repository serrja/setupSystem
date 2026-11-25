import asyncio
import tomllib
from pathlib import Path

import pytest
from setup_plugin_codex import Plugin, Project, Settings, client

from setup_system_sdk import Mutation, Outcome


def test_project_configuration_and_model_availability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    root = tmp_path / "selected"
    (root / ".git").mkdir(parents=True)
    folder = root / ".codex"
    folder.mkdir()
    cfg = folder / "config.toml"
    cfg.write_text('unrelated = "kept"\n')

    async def available(executable: Path) -> dict[str, list[str]]:
        return {"test-model": ["low"]}

    monkeypatch.setattr(client, "models", available)
    selection = Project(project=root)
    plugin = Plugin()
    unavailable = Settings(model="test-model", effort="hidden")
    assert asyncio.run(plugin.plan("configure", unavailable, selection)).outcome == Outcome.BLOCKED
    settings = Settings(model="test-model", effort="low", instructions=True)
    plan = asyncio.run(plugin.plan("configure", settings, selection))
    result = asyncio.run(
        plugin.apply_request(
            "configure",
            Mutation(
                action_id="configure",
                settings=settings.model_dump(mode="json"),
                inputs=selection.model_dump(mode="json"),
                plan=plan,
            ),
        )
    )
    assert result.outcome == Outcome.CHANGED
    assert asyncio.run(plugin.verify(plan, settings, selection)).outcome == Outcome.COMPLETE
    values = tomllib.loads(cfg.read_text())
    assert (
        values["unrelated"] == "kept"
        and values["model"] == "test-model"
        and values["sandbox_mode"] == "workspace-write"
    )
    assert (root / "AGENTS.md").is_file() and not (tmp_path / ".codex").exists()

def test_existing_named_profile_inherits_without_broadening_access(tmp_path,monkeypatch):
    monkeypatch.setenv("HOME",str(tmp_path))
    cfg=tmp_path/".codex/config.toml"
    cfg.parent.mkdir()
    monkeypatch.setenv("CODEX_HOME",str(cfg.parent))
    cfg.write_text('default_permissions = "owner-profile"\napproval_policy="never"\n[permissions.owner-profile]\nextends=":read-only"\n')
    values=tomllib.loads(Plugin().files("configure",Settings(),Project())[cfg].decode())
    assert values["default_permissions"]=="owner-profile" and "sandbox_mode" not in values
    assert values["approval_policy"]=="never"
    with pytest.raises(ValueError, match="existing named profile"):
        Plugin().files("configure",Settings(permission="workspace-write"),Project())
    selected=tomllib.loads(Plugin().files("configure",Settings(permission=":workspace"),Project())[cfg].decode())
    assert selected["default_permissions"]==":workspace" and "owner-profile" in selected["permissions"]
