"""Package receipts reject unknown schemas and preserve historical bytes before migration."""

import json
import os
from pathlib import Path

import pytest

from setup_system.infrastructure.package_journal import historical_status


@pytest.mark.parametrize(
    "stage", ["temporary-network", "temporary-network-cleanup", "user-runtime", "package-downloads"]
)
def test_historical_stages_are_preserved_outside_the_live_vocabulary(
    tmp_path: Path, stage: str
) -> None:
    path = tmp_path / "install.json"
    original = json.dumps(
        {
            "schema": 1,
            "stage": stage,
            "status": "failed",
            "completed": ["metadata"],
            "extra": "prior run",
        }
    ).encode()
    path.write_bytes(original)
    for _ in range(2):
        assert historical_status(path, owner=os.getuid()) == f"failed ({stage})"
    assert path.read_bytes() == original and list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": True},
        {"schema": 3},
        {"stage": "unrecognized"},
        {"status": "broken"},
        {"completed": [1]},
        {"error": {}},
    ],
)
def test_unknown_historical_receipt_is_preserved(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    path = tmp_path / "install.json"
    data = json.dumps({"schema": 1, "stage": "metadata", "status": "running", **changes}).encode()
    path.write_bytes(data)
    assert "unrecognized" in historical_status(path, owner=os.getuid())
    assert path.read_bytes() == data and list(tmp_path.iterdir()) == [path]


def test_current_session_retains_completed_stages_on_failure(tmp_path: Path) -> None:
    path = tmp_path / "install.json"
    data = (
        b'{"schema":2,"stage":"downloads","status":"failed",'
        b'"completed":["metadata","resolution"],"error":"private detail"}'
    )
    path.write_bytes(data)
    assert historical_status(path, owner=os.getuid()) == "failed (downloads)"
    assert path.read_bytes() == data and list(tmp_path.iterdir()) == [path]
