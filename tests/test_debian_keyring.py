"""Check native Signed-By trust, diagnostic redaction and private APT indexes."""

import subprocess
from pathlib import Path

import pytest

from setup_system_sdk import apt

ROOT = Path(__file__).resolve().parents[1]


def test_signed_sources_use_the_regular_trixie_keyring():
    config = (ROOT / "config/base/debian.sources").read_text().split("\n\n")
    assert len(config) == 2
    for stanza in config:
        assert "Signed-By: /usr/share/keyrings/debian-archive-keyring.pgp" in stanza
    assert "URIs: https://deb.debian.org/debian\n" in config[0]
    assert "Suites: trixie trixie-updates trixie-backports" in config[0]
    assert "URIs: https://security.debian.org/debian-security\n" in config[1]


@pytest.mark.parametrize("kind", ["missing", "empty", "symlink"])
def test_invalid_canonical_keyring_stops_before_package_commands(tmp_path, monkeypatch, kind):
    canonical = tmp_path / "canonical.pgp"
    other = tmp_path / "unreviewed"
    other.write_bytes(b"unknown key")
    if kind == "empty":
        canonical.touch()
    elif kind == "symlink":
        canonical.symlink_to(other)
    monkeypatch.setattr(
        apt.subprocess,
        "run",
        lambda *args, **kw: pytest.fail("APT ran before trust validation"),
    )
    with pytest.raises(ValueError):
        apt.keyring(canonical)
    assert other.read_bytes() == b"unknown key"


def test_failed_query_records_a_fixed_localized_error(tmp_path, monkeypatch):
    private = "https://account:credential@fixture.invalid/path?token=private"
    monkeypatch.setattr(
        apt,
        "run_command",
        lambda *args, **kw: subprocess.CompletedProcess(["fixture"], 1, "", private),
    )
    with pytest.raises(ValueError, match="signed package source") as failure:
        apt.query(["fixture"], tmp_path)
    assert private not in str(failure.value)
