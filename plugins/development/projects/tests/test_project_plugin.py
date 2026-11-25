"""Validate project origins and protect repositories in an isolated HOME."""

import asyncio

import pytest
from setup_plugin_projects import FOLDER, Plugin, Settings, project_layout
from setup_plugin_projects.template_models import answer_values, snapshot_answers

from setup_system_sdk import Inputs, Outcome


def test_catalog_origins_and_directory_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for key in ("config", "cache", "data", "state"):
        monkeypatch.setenv("XDG_" + key.upper() + "_HOME", str(tmp_path / key))
    rows = project_layout.load_projects(FOLDER)
    assert len(rows.project) == 22
    assert any(
        r.id == "pokerlost/projectRules" and r.origin.endswith(":pokerlost/projectRules.git")
        for r in rows.project
    )
    plugin = Plugin()
    plan = plugin.prepare("prepare-directories", Settings(), Inputs())
    assert plan.outcome == Outcome.READY and all(not row.cleanup for row in plan.resources)
    outcome = plugin.mutate("prepare-directories", Settings(), Inputs(), plan)
    assert outcome.outcome == Outcome.CHANGED
    assert asyncio.run(plugin.verify(plan, Settings(), Inputs())).outcome == Outcome.COMPLETE
    assert plugin.prepare("prepare-directories", Settings(), Inputs()).outcome == Outcome.NO_CHANGES
    assert (
        "pokerlost/projectRules" in plugin.prepare("inspect-projects", Settings(), Inputs()).summary
    )


def test_existing_git_root_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    conflict = tmp_path / "Projects/.git"
    conflict.mkdir(parents=True)
    with pytest.raises(ValueError, match="Git repository"):
        Plugin().prepare("prepare-directories", Settings(), Inputs())
    assert conflict.is_dir()


def test_copier_answers_are_deep_snapshots() -> None:
    initial = {"nested": [{"choices": ["original"]}]}
    snapshot = snapshot_answers(initial)
    initial["nested"][0]["choices"].append("changed")
    answer_values(snapshot)["nested"].append("changed")
    assert answer_values(snapshot) == {"nested": [{"choices": ["original"]}]}
    with pytest.raises(ValueError, match="Internal"):
        snapshot_answers({"_src_path": "other"})
