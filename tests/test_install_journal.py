"""User stages merge into the latest system journal without losing completed package work."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from setup_system import installation


def journal(tmp_path: Path) -> installation.SystemPaths:
    paths = installation.SystemPaths(journal=tmp_path / "state/install.json")
    installation._write_system(
        paths.journal,
        (
            json.dumps(
                {
                    "schema": 1,
                    "status": "system-complete",
                    "stage": "user-runtime",
                    "completed": [
                        "metadata",
                        "resolution",
                        "downloads",
                        "packages",
                        "source",
                        "happ-service",
                    ],
                }
            )
            + "\n"
        ).encode(),
    )
    return paths


def test_failure_uses_actual_user_download_stage_and_preserves_package_progress(
    tmp_path: Path,
) -> None:
    paths = journal(tmp_path)
    before = json.loads(paths.journal.read_text())
    installation.mark_user_stage(paths, "failed", stage="user-downloads")
    state = json.loads(paths.journal.read_text())
    assert state["status"] == "failed" and state["stage"] == "user-downloads"
    assert state["completed"] == before["completed"]


def test_completion_merges_latest_journal_and_is_idempotent(tmp_path: Path) -> None:
    paths = journal(tmp_path)
    latest = json.loads(paths.journal.read_text())
    latest["completed"].append("user-downloads")
    latest["observation"] = "latest package-context receipt"
    installation._write_system(paths.journal, (json.dumps(latest) + "\n").encode())
    for _ in range(2):
        installation.mark_user_stage(
            paths,
            "complete",
            completed=("user-downloads", "user-configuration"),
        )
    state = json.loads(paths.journal.read_text())
    assert state["status"] == "complete" and state["stage"] == "user"
    assert state["observation"] == latest["observation"]
    assert state["completed"] == [*latest["completed"], "user-configuration"]


@pytest.mark.parametrize(
    ("status", "stage", "completed"),
    [
        ("unknown", "user", ()),
        ("complete", "unknown-stage", ()),
        ("complete", "user", ("unverified-stage",)),
    ],
)
def test_invalid_user_updates_cannot_change_the_journal(
    tmp_path: Path,
    status: str,
    stage: str,
    completed: tuple[str, ...],
) -> None:
    paths = journal(tmp_path)
    before = paths.journal.read_bytes()
    with pytest.raises(ValueError):
        installation.mark_user_stage(paths, status, stage=stage, completed=completed)
    assert paths.journal.read_bytes() == before
