"""Versioned envelopes, bounded native input and typed outcomes."""

import pytest
from pydantic import ValidationError

from setup_system_sdk import Outcome, Result
from setup_system_sdk.protocol import Request


def test_private_operation_reader_rejects_links_and_excess_without_reading(tmp_path):
    import os

    from setup_system.domain.contracts import MAX_MESSAGE_BYTES
    from setup_system.infrastructure.operation_io import read_wire

    request = tmp_path / "request.json"
    request.write_bytes(b"{}")
    request.chmod(0o600)
    assert read_wire(request) == b"{}"
    link = tmp_path / "link"
    link.symlink_to(request)
    with pytest.raises(OSError):
        read_wire(link)
    link.unlink()
    os.link(request, link)
    with pytest.raises(ValueError):
        read_wire(request)
    link.unlink()
    with request.open("wb") as stream:
        stream.truncate(MAX_MESSAGE_BYTES + 1)
    with pytest.raises(ValueError):
        read_wire(request)


@pytest.mark.parametrize("outcome", list(Outcome))
def test_result_roundtrip_preserves_protocol_outcomes(outcome):
    result = Result(outcome=outcome, message="localized label")
    assert Result.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    "extra",
    [
        {"protocol": 2},
        {"method": "exec"},
        {"inputs": {"items": [float("nan")]}},
        {"target": "unreviewed"},
    ],
)
def test_unknown_wire_variants_are_rejected(extra):
    with pytest.raises(ValidationError):
        Request.model_validate({"plugin_id": "demo", "method": "plan", **extra})
