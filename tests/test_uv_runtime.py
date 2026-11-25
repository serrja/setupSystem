"""Exercise the first extension activation on a target without a system uv."""

import asyncio
import hashlib
import io
import tarfile

import pytest

from setup_system.infrastructure import uv_runtime


def test_missing_uv_fetches_verified_tool_once_and_reuses_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    member = "uv-x86_64-unknown-linux-gnu/uv"
    payload = b"fixture executable"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        info = tarfile.TarInfo(member)
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    blob = buf.getvalue()
    monkeypatch.setattr(uv_runtime.shutil, "which", lambda *args, **kwargs: None)
    monkeypatch.setattr(uv_runtime.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(uv_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setitem(uv_runtime.ARCHIVES, "x86_64", hashlib.sha256(blob).hexdigest())
    urls = []

    async def download(url, policy):
        urls.append(url)
        assert "github.com" in policy.allowed_hosts
        return blob

    monkeypatch.setattr(uv_runtime, "download_https", download)
    for name in ("first", "offline"):
        stage = tmp_path / name
        stage.mkdir()
        assert asyncio.run(uv_runtime.executable(stage)) == str(stage / "uv")
        assert (stage / "uv").read_bytes() == payload
    assert len(urls) == 1
    next(tmp_path.rglob(".uv-*.tar.gz")).write_bytes(b"changed")
    with pytest.raises(ValueError, match="trusted release digest"):
        asyncio.run(uv_runtime.executable(tmp_path))
    assert not (tmp_path / "uv").exists()
