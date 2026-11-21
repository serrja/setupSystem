"""Package receipts reject unknown schemas and preserve historical bytes before migration."""

import hashlib
import json
from pathlib import Path

import pytest

from setup_system.domain.package_journal import InstallJournal, InstallStage, InstallStatus
from setup_system.infrastructure.package_journal import prepare_journal


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
    prepare_journal(path)
    prepare_journal(path)
    backup = (
        tmp_path / "migration" / ("install-v1-" + hashlib.sha256(original).hexdigest() + ".json")
    )
    assert backup.read_bytes() == path.read_bytes() == original
    with pytest.raises(ValueError):
        InstallStage(stage)


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
    with pytest.raises(ValueError, match="journal"):
        prepare_journal(path)
    assert path.read_bytes() == data
    assert list(tmp_path.iterdir()) == [path]


def test_current_session_retains_completed_stages_on_failure(tmp_path: Path) -> None:
    journal = (
        InstallJournal()
        .checkpoint(InstallStage.RESOLUTION, "metadata")
        .checkpoint(InstallStage.DOWNLOADS, "resolution")
    )
    failed = journal.failed(RuntimeError("private detail"))
    path = tmp_path / "install.json"
    path.write_bytes(failed.serialize())
    prepare_journal(path)
    assert list(tmp_path.iterdir()) == [path]
    assert failed.completed == (InstallStage.METADATA, InstallStage.RESOLUTION)
    assert failed.status == InstallStatus.FAILED and failed.error == "RuntimeError"
    assert b"private detail" not in path.read_bytes()
