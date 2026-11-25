from __future__ import annotations

from pathlib import Path

import pytest
from setup_plugin_rtk import runtime as rtk

from setup_system.application.recovery import restore


@pytest.fixture
def selected_rtk() -> rtk.Release:
    return rtk.Release(
        version="1.2.3",
        archive="rtk-x86_64-unknown-linux-musl.tar.gz",
        sha256="a" * 64,
        checksum_sha256="b" * 64,
    )


def test_rtk_detects_product_and_preserves_pinned_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, selected_rtk: rtk.Release
) -> None:
    version = selected_rtk.version
    binary = tmp_path / "rtk"
    binary.write_text(
        f'#!/bin/sh\nif [ "$1" = "--version" ]; then echo "rtk {version}"; '
        'else echo "Show token savings summary"; fi\n'
    )
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    monkeypatch.setattr(rtk, "_released_binary", lambda selected: binary.read_bytes())
    status, action = rtk.plan_rtk(selected_rtk)
    assert status.product_matches and status.version == version
    assert action.startswith("unchanged:")
    assert rtk.install_rtk(selected_rtk) == (action, None)
    assert not (tmp_path / "source" / "backups").exists()


def test_rtk_blocks_same_named_other_product(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, selected_rtk: rtk.Release
) -> None:
    version = selected_rtk.version
    binary = tmp_path / "rtk"
    binary.write_text(f'#!/bin/sh\necho "rtk {version}"\n')
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    monkeypatch.setattr(rtk, "_released_binary", lambda selected: b"official different bytes")
    monkeypatch.setattr(
        rtk, "_verify_executable", lambda *args: pytest.fail("unknown executable ran")
    )
    _, action = rtk.plan_rtk(selected_rtk)
    assert action.startswith("blocked:")
    with pytest.raises(ValueError, match="not rtk-ai/rtk"):
        rtk.install_rtk(selected_rtk)


def test_downloaded_rtk_archive_and_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib
    import io
    import tarfile

    home = tmp_path / "rtk home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "state"))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    data = b'#!/bin/sh\ncase "$1" in --version) echo "rtk 1.2.3";; *) echo "token savings";; esac\n'
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as tar:
        entry = tarfile.TarInfo("bin/rtk")
        entry.size = len(data)
        tar.addfile(entry, io.BytesIO(data))
    archive = stream.getvalue()
    name = "rtk-x86_64-unknown-linux-musl.tar.gz"
    digest = hashlib.sha256(archive).hexdigest()
    checksums = f"{digest}  {name}\n".encode()
    selected = rtk.Release(
        version="1.2.3",
        archive=name,
        sha256=digest,
        checksum_sha256=hashlib.sha256(checksums).hexdigest(),
    )

    def transport(url, limit):
        if url.endswith("/checksums.txt"):
            return checksums
        assert url == f"https://github.com/rtk-ai/rtk/releases/download/v1.2.3/{name}"
        return archive

    monkeypatch.setattr(rtk, "_read", transport)
    target = home / ".local/bin/rtk"
    with pytest.raises(ValueError, match="checksums digest"):
        rtk.install_rtk(selected.validated(checksum_sha256="0" * 64), update=True)
    assert not target.exists()
    _, receipt = rtk.install_rtk(selected, update=True)
    assert target.read_bytes() == data and receipt.is_dir()
    assert rtk.install_rtk(selected, update=True)[1] is None
    restore(receipt.name, apply=True)
    assert not target.exists()
