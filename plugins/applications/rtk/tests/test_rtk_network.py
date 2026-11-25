"""Official tool downloads redact failures before returning to the desktop form."""

import httpx
import pytest
from setup_plugin_rtk import runtime as rtk
from test_downloads import mock_https


@pytest.mark.parametrize("status", [403, 404, 429])
def test_rtk_http_failure_reports_status_and_host_without_url_secrets(
    monkeypatch, status: int
) -> None:
    url = "https://github.com/private-subscription-token?access_token=secret"

    def fail(*args, **kwargs):
        return httpx.Response(status)

    mock_https(monkeypatch, fail)
    with pytest.raises(RuntimeError) as caught:
        rtk._read(url, 1024)
    message = str(caught.value)
    assert str(status) in message and "github.com" in message
    assert all(
        secret not in message
        for secret in ("password", "private-subscription-token", "access_token", "secret")
    )


def test_rtk_network_failure_does_not_disclose_proxy_credentials(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise httpx.ConnectError("proxy password=private-value")

    mock_https(monkeypatch, fail)
    with pytest.raises(RuntimeError) as caught:
        rtk._read("https://github.com/rtk-ai/rtk/releases/download/v1.2.3/checksums.txt", 1024)
    assert "github.com" in str(caught.value)
    assert "private-value" not in str(caught.value)


def test_rtk_download_identifies_installer_without_changing_pinned_url(monkeypatch) -> None:
    url = "https://github.com/rtk-ai/rtk/releases/download/v1.2.3/checksums.txt"

    def download(request):
        assert str(request.url) == url
        assert request.headers["User-agent"] == "setupSystem"
        assert request.extensions["timeout"]["read"] == 30
        return httpx.Response(
            200, stream=httpx.ByteStream(b"verified later by the pinned archive digest")
        )

    mock_https(monkeypatch, download)
    assert rtk._read(url, 1024) == b"verified later by the pinned archive digest"
