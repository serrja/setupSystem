"""Privileged acquisition validates caller, HTTPS, provenance and archive boundaries."""

import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from setup_system.infrastructure import trusted_bootstrap as bootstrap
from setup_system.infrastructure.privileged import PrivilegedExecutor


def test_sudo_caller_must_match_all_passwd_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_UID", str(os.getuid()))
    monkeypatch.setenv("SUDO_GID", str(os.getgid() + 1))
    monkeypatch.setenv("SUDO_USER", "fixture")
    with pytest.raises(ValueError):
        bootstrap.caller()


@pytest.mark.parametrize("uid", ["", "0", "-1", "not-a-uid"])
def test_unsafe_sudo_uid_rejected_before_network(monkeypatch: pytest.MonkeyPatch, uid: str) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_UID", uid)
    with pytest.raises(ValueError):
        bootstrap.caller()


def test_executor_uses_system_python_and_data_on_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    calls = []
    monkeypatch.setattr("subprocess.run", lambda argv, **kwargs: calls.append((argv, kwargs)))
    PrivilegedExecutor("v1.2.3", "a" * 40).run("base", {"hyprland": True})
    argv, options = calls[0]
    assert argv[:8] == ["/usr/bin/sudo", "-n", "--", "/usr/bin/python3", "-I", "-B", "-S", "-c"]
    assert json.loads(options["input"])["action"] == "base"
    assert "--source" not in argv
    assert "PYTHONPATH" not in options["env"]
    assert "pip install" not in argv[-1] and "uv sync" not in argv[-1]


def archive(member: str, kind: bytes = tarfile.REGTYPE) -> bytes:
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        entry = tarfile.TarInfo(member)
        entry.type = kind
        entry.linkname = "/etc/passwd"
        if kind == tarfile.REGTYPE:
            entry.size = 4
            tar.addfile(entry, io.BytesIO(b"data"))
        else:
            tar.addfile(entry)
    return data.getvalue()


@pytest.mark.parametrize(
    "member,kind",
    [
        ("../outside", tarfile.REGTYPE),
        ("/absolute", tarfile.REGTYPE),
        ("setup_system-1.2.3/link", tarfile.SYMTYPE),
        ("other/source", tarfile.REGTYPE),
    ],
)
def test_unsafe_privileged_archives_are_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, member: str, kind: bytes
) -> None:
    raw = archive(member, kind)
    digest = hashlib.sha256(raw).hexdigest()

    def fetch(url, limit):
        if url.endswith("python-build-receipt.json"):
            return json.dumps(
                {
                    "schemaVersion": 1,
                    "sourceSha": "a" * 40,
                    "files": [{"name": "setup_system-1.2.3.tar.gz", "sha256": digest}],
                }
            ).encode()
        if url.endswith("checksums.sha256"):
            return f"{digest}  setup_system-1.2.3.tar.gz\n".encode()
        return raw

    monkeypatch.setattr(bootstrap, "fetch", fetch)
    # Redirect only the protected state destination into this disposable fixture.
    real_path = Path
    monkeypatch.setattr(
        bootstrap,
        "Path",
        lambda value: (
            tmp_path / "state" if value == "/var/lib/setup-system/releases" else real_path(value)
        ),
    )
    monkeypatch.setattr(bootstrap, "protected", lambda *args, **kwargs: None)
    with pytest.raises(ValueError, match="Unsafe"):
        bootstrap.acquire("v1.2.3", "a" * 40)
    assert not (tmp_path / "outside").exists()


def test_native_provenance_mismatch_stops_before_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bootstrap,
        "fetch",
        lambda *args: json.dumps({"schemaVersion": 1, "sourceSha": "b" * 40}).encode(),
    )
    with pytest.raises(ValueError, match="provenance"):
        bootstrap.acquire("v1.2.3", "a" * 40)


def test_operation_lock_excludes_another_executor_and_releases_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    original = os.fstat

    def metadata(descriptor):
        info = original(descriptor)
        return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_nlink=info.st_nlink)

    monkeypatch.setattr(bootstrap, "protected", lambda path: None)
    monkeypatch.setattr(os, "fstat", metadata)
    with pytest.raises(ValueError, match="synthetic"):
        with bootstrap.operation_lock(tmp_path):
            with pytest.raises(RuntimeError, match="Another system"):
                with bootstrap.operation_lock(tmp_path):
                    pytest.fail("Concurrent executor entered")
            raise ValueError("synthetic")
    with bootstrap.operation_lock(tmp_path):
        pass
