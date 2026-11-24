"""The RTK release API and archive use separately restricted HTTPS origins."""

import json

import pytest
from test_downloads import scripted_https

from setup_system.infrastructure import downloads, rtk

API = "https://api.github.com/repos/rtk-ai/rtk/releases/latest"
BASE = "https://github.com/rtk-ai/rtk/releases/download/v1.2.3"


@pytest.mark.parametrize(
    ("url", "redirect"),
    [
        (API, "https://github.com/other/release"),
        (BASE + "/file", "https://untrusted.invalid/token"),
        (BASE + "/file", "http://github.com/downgrade"),
    ],
)
def test_untrusted_redirect_is_rejected_before_contact(monkeypatch, url, redirect):
    visited = scripted_https(monkeypatch, [redirect])
    with pytest.raises(downloads.DownloadError) as caught:
        rtk._read(url, 64)
    assert visited == [url] and caught.value.kind == downloads.DownloadFailure.URL


@pytest.mark.parametrize("size", [63, 64, 65])
def test_asset_redirect_keeps_only_bounded_bytes(monkeypatch, size):
    start = BASE + "/file"
    end = "https://release-assets.githubusercontent.com/file"
    data = b"x" * size
    seen = scripted_https(monkeypatch, [end], data)
    if size > 64:
        with pytest.raises(downloads.DownloadError, match="size limit"):
            rtk._read(start, 64)
    else:
        assert rtk._read(start, 64) == data
    assert seen == [start, end]


def metadata(name):
    url = BASE.replace("download/v", "tag/v")
    return {
        "draft": False,
        "prerelease": False,
        "tag_name": "v1.2.3",
        "published_at": "2026-11-01",
        "html_url": url,
        "assets": [
            {
                "name": filename,
                "browser_download_url": BASE + "/" + filename,
                "digest": "sha256:" + "a" * 64,
            }
            for filename in (name, "checksums.txt")
        ],
    }


@pytest.mark.parametrize(
    ("machine", "filename"),
    [
        ("amd64", "rtk-x86_64-unknown-linux-musl.tar.gz"),
        ("x86_64", "rtk-x86_64-unknown-linux-musl.tar.gz"),
        ("arm64", "rtk-aarch64-unknown-linux-gnu.tar.gz"),
        ("aarch64", "rtk-aarch64-unknown-linux-gnu.tar.gz"),
    ],
)
def test_official_release_resolves_native_architecture_aliases(monkeypatch, machine, filename):
    monkeypatch.setattr(rtk.platform, "system", lambda: "Linux")
    monkeypatch.setattr(rtk.platform, "machine", lambda: machine)
    seen = scripted_https(monkeypatch, [], json.dumps(metadata(filename)).encode())
    selected = rtk.release()
    assert selected.archive == filename
    assert seen == [API] and selected.version == "1.2.3"


@pytest.mark.parametrize(
    "variant", ["duplicate", "other_project", "unpublished", "private_url", "missing_tag"]
)
def test_incomplete_or_substituted_official_metadata_fails_closed(monkeypatch, variant):
    monkeypatch.setattr(rtk.platform, "system", lambda: "Linux")
    monkeypatch.setattr(rtk.platform, "machine", lambda: "x86_64")
    data = metadata("rtk-x86_64-unknown-linux-musl.tar.gz")
    if variant == "duplicate":
        data["assets"].append(data["assets"][0])
    elif variant == "other_project":
        data["html_url"] = data["html_url"].replace("/rtk-ai/", "/other/")
    elif variant == "unpublished":
        data["published_at"] = None
    elif variant == "private_url":
        data["assets"][0]["browser_download_url"] = "http://127.0.0.1/hidden"
    else:
        del data["tag_name"]
    seen = scripted_https(monkeypatch, [], json.dumps(data).encode())
    with pytest.raises(ValueError):
        rtk.release()
    assert seen == [API]


@pytest.mark.parametrize(
    "variant", ["relative_escape", "absolute", "symlink", "duplicate", "unpacked_limit"]
)
def test_rtk_archive_never_publishes_unsafe_entries(tmp_path, monkeypatch, variant):
    import hashlib
    import io
    import tarfile

    name = "rtk-x86_64-unknown-linux-musl.tar.gz"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for _ in range(2 if variant == "duplicate" else 1):
            member = tarfile.TarInfo(
                {"relative_escape": "../rtk", "absolute": "/rtk"}.get(variant, "rtk")
            )
            if variant == "symlink":
                member.type = tarfile.SYMTYPE
                member.linkname = "/outside"
            else:
                member.size = 1
            tar.addfile(member, io.BytesIO(b"x"))
    archive = buffer.getvalue()
    digest = hashlib.sha256(archive).hexdigest()
    checksums = f"{digest}  {name}\n".encode()
    if variant == "unpacked_limit":
        monkeypatch.setattr(rtk, "MAX_ARCHIVE_BYTES", len(archive))
    monkeypatch.setattr(
        rtk, "_read", lambda url, limit: checksums if url.endswith("checksums.txt") else archive
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    selected = rtk.Release(
        version="1.2.3",
        archive=name,
        sha256=digest,
        checksum_sha256=hashlib.sha256(checksums).hexdigest(),
    )
    with pytest.raises(ValueError, match=r"bounded regular|unpacked size"):
        rtk.install_rtk(selected)
    assert not list(tmp_path.iterdir())
