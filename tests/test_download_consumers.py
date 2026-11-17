"""Exercise asset consumers through the real shared transport without network or host apply."""

from __future__ import annotations

import hashlib
import io
import tarfile
import zipfile
from pathlib import Path
from urllib.request import Request

import pytest

from setup_system import downloads
from setup_system.adapters.debian import docker
from setup_system.application import rtk, terminal_dev
from setup_system.planning import Platform


class Response(io.BytesIO):
    status = 200

    def __init__(self, content: bytes, url: str) -> None:
        super().__init__(content)
        self.url = url

    def geturl(self) -> str:
        return self.url


def synthetic_https(
    monkeypatch: pytest.MonkeyPatch, content: bytes, *, redirect: str | None = None
) -> list[str]:
    requested: list[str] = []

    def build(*handlers: object) -> object:
        policy = next(h for h in handlers if isinstance(h, downloads.HTTPSRedirects))

        class Opener:
            def open(self, request: Request, *, timeout: float) -> Response:
                requested.append(request.full_url)
                if redirect:
                    result = policy.redirect_request(request, None, 307, "Redirect", {}, redirect)
                    assert isinstance(result, Request)
                    requested.append(result.full_url)
                    return Response(content, result.full_url)
                return Response(content, request.full_url)

        return Opener()

    monkeypatch.setattr(downloads, "build_opener", build)
    return requested


@pytest.mark.parametrize("consumer", ["terminal", "rtk", "docker"])
def test_asset_consumers_reject_intermediate_untrusted_redirects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, consumer: str
) -> None:
    requested = synthetic_https(
        monkeypatch, b"untrusted", redirect="https://unexpected.invalid/secret-path?token=hidden"
    )
    with pytest.raises(RuntimeError) as error:
        if consumer == "terminal":
            terminal_dev._download_archive(
                "https://github.com/owner/tool",
                tmp_path / "asset",
                64,
                redirect_error="Unexpected binary release redirect",
                size_error="Too large",
            )
        elif consumer == "rtk":
            rtk._download("https://github.com/rtk-ai/rtk/asset", tmp_path / "asset")
        else:
            docker._download_signing_key()
    assert len(requested) == 1
    assert "secret-path" not in str(error.value) and "hidden" not in str(error.value)
    assert "unexpected.invalid" not in str(error.value)


@pytest.mark.parametrize("consumer", ["terminal", "rtk", "docker"])
def test_asset_consumers_enforce_bounded_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, consumer: str
) -> None:
    size = 64 * 1024 if consumer == "docker" else 8
    synthetic_https(monkeypatch, b"x" * (size + 1))
    with pytest.raises(RuntimeError, match="size limit"):
        if consumer == "terminal":
            terminal_dev._download_archive(
                "https://github.com/owner/tool",
                tmp_path / "asset",
                size,
                redirect_error="Unexpected redirect",
                size_error="Archive exceeds size limit",
            )
        elif consumer == "rtk":
            rtk._download("https://github.com/rtk-ai/rtk/asset", tmp_path / "asset", max_bytes=size)
        else:
            docker._download_signing_key()
    if (tmp_path / "asset").exists():
        assert (tmp_path / "asset").stat().st_size <= size


@pytest.mark.parametrize("consumer", ["terminal", "rtk"])
def test_github_release_redirects_keep_approved_bytes_and_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, consumer: str
) -> None:
    content = b"verified fixture asset"
    requested = synthetic_https(
        monkeypatch, content, redirect="https://release-assets.githubusercontent.com/fixture"
    )
    destination = tmp_path / "asset"
    if consumer == "terminal":
        result = terminal_dev._download_archive(
            "https://github.com/owner/tool",
            destination,
            64,
            redirect_error="Unexpected redirect",
            size_error="Too large",
        )
        assert result == hashlib.sha256(content).hexdigest()
    else:
        rtk._download("https://github.com/rtk-ai/rtk/asset", destination)
    assert destination.read_bytes() == content and len(requested) == 2


def archive(binary: str, *, zipped: bool) -> bytes:
    data = io.BytesIO()
    content = b"fixture binary; never executed"
    if zipped:
        with zipfile.ZipFile(data, "w") as bundle:
            bundle.writestr(binary, content)
    else:
        with tarfile.open(fileobj=data, mode="w:gz") as bundle:
            item = tarfile.TarInfo(binary)
            item.size = len(content)
            item.mode = 0o755
            bundle.addfile(item, io.BytesIO(content))
    return data.getvalue()


@pytest.mark.parametrize("provider", ["neovim", "user_binary", "archive_tool"])
@pytest.mark.parametrize("valid_digest", [True, False])
def test_terminal_providers_preserve_hash_verification_and_repeat_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, valid_digest: bool
) -> None:
    home = tmp_path / "private home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "data"))
    version = "1.2.3"
    path = "nvim-linux-x86_64/bin/nvim" if provider == "neovim" else "fixture-tool"
    content = archive(path, zipped=provider == "archive_tool")
    synthetic_https(monkeypatch, content)
    digest = hashlib.sha256(content).hexdigest() if valid_digest else "0" * 64
    entry = {
        "version": version,
        "linux_x86_64_url": "https://github.com/owner/tool",
        "linux_x86_64_sha256": digest,
    }
    if provider == "user_binary":
        entry = {"version": version, "url": "https://github.com/owner/tool", "sha256": digest}
    manifest = (
        {"neovim": entry}
        if provider == "neovim"
        else {
            "userBinary" if provider == "user_binary" else "archiveTools": {"fixture-tool": entry}
        }
    )
    monkeypatch.setattr(terminal_dev, "_manifest", lambda _: manifest)
    monkeypatch.setattr(
        terminal_dev,
        "_run",
        lambda *_a, **_k: "NVIM v1.2.3" if provider == "neovim" else "fixture-tool 1.2.3",
    )
    platform = Platform("debian", "13", "x86_64", True, False, "fixture")

    def install() -> str:
        if provider == "neovim":
            return terminal_dev.install_neovim(tmp_path, platform)
        if provider == "user_binary":
            return terminal_dev._verified_user_binary(tmp_path, "fixture-tool")
        return terminal_dev.install_archive_tool(tmp_path, platform, "fixture-tool")

    if not valid_digest:
        with pytest.raises(RuntimeError, match="digest mismatch"):
            install()
        assert not (
            home / ".local/bin" / ("nvim" if provider == "neovim" else "fixture-tool")
        ).exists()
    else:
        assert install().startswith("changed:")
        assert install().startswith("unchanged:")


@pytest.mark.parametrize(
    ("machine", "expected"),
    [
        ("x86_64", "amd64.tar.gz"),
        ("amd64", "amd64.tar.gz"),
        ("aarch64", "arm64.tar.gz"),
        ("arm64", "arm64.tar.gz"),
    ],
)
def test_rtk_asset_architecture_uses_the_shared_alias_policy(
    monkeypatch: pytest.MonkeyPatch, machine: str, expected: str
) -> None:
    monkeypatch.setattr(rtk.platform, "system", lambda: "Linux")
    monkeypatch.setattr(rtk.platform, "machine", lambda: machine)
    assert (
        rtk._target_name({"linux_x86_64": "amd64.tar.gz", "linux_arm64": "arm64.tar.gz"})
        == expected
    )
