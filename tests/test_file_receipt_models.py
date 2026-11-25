"""Keep receipt validation independent of storage, current HOME and XDG."""

import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


def test_receipt_json_round_trip_does_not_import_storage(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            """
import sys
from setup_system.domain.receipts import Operation
payload = '{"run_id":"20260101T000000Z-01234567","files":[],"status":"partial"}'
record = Operation.model_validate_json(payload)
assert Operation.model_validate_json(record.model_dump_json()) == record
assert not any(name.startswith("setup_system.infrastructure") for name in sys.modules)
""",
        ],
        cwd=tmp_path,
        env={"HOME": str(tmp_path / "missing"), "XDG_STATE_HOME": str(tmp_path / "state")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_receipt_validation_rejects_ambiguous_before_evidence() -> None:
    from setup_system.domain.receipts import FileChange, FileState, Operation

    state = FileState(sha256="a" * 64, mode=0o600, uid=1, gid=1)
    row = FileChange(
        relative="settings", before=None, after=state, backup=None, previously_owned=False
    )
    with pytest.raises(ValidationError):
        FileState.model_validate({**state.model_dump(), "mode": "384"})
    with pytest.raises(ValidationError):
        FileChange.model_validate({**row.model_dump(), "backup": "files/settings"})
    with pytest.raises(ValidationError):
        Operation(run_id="20260101T000000Z-01234567", files=[row, row])


@pytest.mark.parametrize("version", [True, 1.0, "1", 2])
def test_schema_versions_only_accept_the_published_integer(
    version: bool | int | float | str,
) -> None:
    from setup_system.domain.receipts import Operation, Ownership

    for model, values in (
        (Operation, {"run_id": "20260101T000000Z-01234567", "files": []}),
        (Ownership, {}),
    ):
        with pytest.raises(ValidationError):
            model.model_validate({**values, "schema_version": version})
