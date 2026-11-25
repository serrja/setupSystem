"""Record only bounded mutation metadata after safely completing native work."""

import asyncio
import json
import threading

import pytest

from setup_system.infrastructure import operation_outcome
from setup_system.infrastructure.operation_outcome import read_last_outcome, record_outcome
from setup_system.models import Status


@pytest.fixture
def receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path / "state/setup-system/last-operation.json"


def test_read_is_side_effect_free_and_updates_contain_only_protocol_fields(receipt):
    assert asyncio.run(read_last_outcome()) is None and not receipt.parent.exists()
    for status in (Status.PARTIAL, Status.UNKNOWN, Status.COMPLETE):
        asyncio.run(record_outcome("base-apply", status))
        record = asyncio.run(read_last_outcome())
        assert record.status == status and record.target.value == "base-apply"
        assert record.describe() == f"Last operation: base-apply — {status.value}."
        assert receipt.stat().st_mode & 0o777 == 0o600
        assert set(json.loads(receipt.read_bytes())) == {"target", "status", "recorded_at"}


@pytest.mark.parametrize("unsafe", ["malformed", "overlong", "link", "mode"])
def test_unknown_or_unsafe_receipt_is_preserved(receipt, unsafe):
    receipt.parent.mkdir(parents=True)
    original = receipt.with_suffix(".original")
    original.write_text("keep")
    if unsafe == "link":
        receipt.symlink_to(original)
    else:
        receipt.write_text("X" * 513 if unsafe == "overlong" else "bad")
        receipt.chmod(0o644 if unsafe == "mode" else 0o600)
    before = receipt.read_bytes()
    with pytest.raises((ValueError, OSError)):
        asyncio.run(record_outcome("base-apply", Status.UNKNOWN))
    assert receipt.read_bytes() == before and original.read_text() == "keep"


def test_read_only_target_or_pending_status_creates_no_receipt(receipt):
    for target, status in (
        ("base-plan", Status.READY),
        ("invalid", Status.UNKNOWN),
        ("admin-apply", Status.PLAN_READY),
    ):
        with pytest.raises(ValueError):
            asyncio.run(record_outcome(target, status))
    assert not receipt.parent.exists()


def test_cancelling_writer_waits_for_the_receipt(receipt, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    publish = operation_outcome.atomic_write

    def pause(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return publish(*args, **kwargs)

    monkeypatch.setattr(operation_outcome, "atomic_write", pause)

    async def run():
        writer = asyncio.create_task(record_outcome("admin-apply", Status.UNKNOWN))
        assert await asyncio.to_thread(started.wait, 5)
        writer.cancel()
        await asyncio.sleep(0)
        assert not writer.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await writer
        assert (await read_last_outcome()).status == Status.UNKNOWN

    asyncio.run(run())
