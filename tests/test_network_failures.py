"""Pinned runtime downloads report HTTP failures without exposing URL secrets."""

from urllib.error import HTTPError, URLError

import pytest

from setup_system import runtime


@pytest.mark.parametrize("status", [403, 404, 429])
def test_uv_http_failure_reports_status_and_host_without_url_secrets(
    monkeypatch, status: int
) -> None:
    url = "https://name:password@github.com/private-subscription-token?access_token=secret"

    def fail(*args, **kwargs):
        raise HTTPError(url, status, "Denied", {}, None)

    monkeypatch.setattr(runtime, "urlopen", fail)
    with pytest.raises(RuntimeError) as caught:
        runtime._read_limited(url, 1024)
    message = str(caught.value)
    assert str(status) in message and "github.com" in message
    assert all(
        secret not in message
        for secret in ("password", "private-subscription-token", "access_token", "secret")
    )


def test_uv_network_failure_does_not_disclose_proxy_credentials(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise URLError("proxy password=private-value")

    monkeypatch.setattr(runtime, "urlopen", fail)
    with pytest.raises(RuntimeError) as caught:
        runtime._read_limited("https://releases.astral.sh/pinned-asset", 1024)
    assert "releases.astral.sh" in str(caught.value)
    assert "private-value" not in str(caught.value)


def test_uv_download_identifies_installer_without_changing_pinned_url(monkeypatch) -> None:
    from urllib.request import Request

    url = runtime.RELEASE_BASE + "/uv-x86_64-unknown-linux-gnu.tar.gz"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def geturl(self):
            return url

        def read(self, size):
            assert size == 1025
            return b"verified later by the pinned archive digest"

    def download(request, *, timeout):
        assert isinstance(request, Request)
        assert request.full_url == url
        assert request.get_header("User-agent") == "setupSystem"
        assert timeout == 30
        return Response()

    monkeypatch.setattr(runtime, "urlopen", download)
    assert runtime._read_limited(url, 1024) == b"verified later by the pinned archive digest"
