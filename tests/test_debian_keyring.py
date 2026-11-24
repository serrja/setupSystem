"""Check native Signed-By trust, diagnostic redaction and private APT indexes."""

import hashlib
import runpy
from pathlib import Path

import pytest

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
    scope = runpy.run_path(str(ROOT / "ansible/files/apt_apply.py"))
    canonical = tmp_path / "canonical.pgp"
    other = tmp_path / "unreviewed"
    other.write_bytes(b"unknown key")
    if kind == "empty":
        canonical.touch()
    elif kind == "symlink":
        canonical.symlink_to(other)
    monkeypatch.setattr(
        scope["subprocess"],
        "run",
        lambda *args, **kw: pytest.fail("APT ran before trust validation"),
    )
    with pytest.raises(ValueError, match="keyring"):
        scope["keyring"](canonical)
    assert other.read_bytes() == b"unknown key"


def test_native_failed_task_diagnostic_preserves_reason_without_credentials(tmp_path):
    filters = runpy.run_path(str(ROOT / "ansible/filter_plugins/native.py"))[
        "FilterModule"
    ]().filters()
    value = hashlib.sha256(str(tmp_path).encode()).hexdigest()
    message = (
        "signature verification failed https://"
        + value
        + ":"
        + value
        + "@example.invalid/path?token="
        + value
        + " password="
        + value
    )
    redacted = filters["native_error"](message)
    assert "signature verification failed" in redacted and value not in redacted
    assert not list(tmp_path.iterdir())
