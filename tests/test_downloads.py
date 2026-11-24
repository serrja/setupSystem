"""Transport policy is exercised with urllib handlers and no external requests."""

from __future__ import annotations

import io
from email.message import Message
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, ProxyHandler, build_opener
from urllib.response import addinfourl

import pytest

from setup_system.infrastructure import downloads


def scripted_https(
    monkeypatch, destinations: list[str], body: bytes = b"fixture", *, status: int = 200
):
    visited: list[str] = []

    class SyntheticHTTPS(HTTPSHandler):
        def https_open(self, request):
            visited.append(request.full_url)
            assert request.get_header("User-agent") == downloads.USER_AGENT
            headers = Message()
            if len(visited) <= len(destinations):
                headers["Location"] = destinations[len(visited) - 1]
                code, content = 302, b""
            else:
                code, content = status, body
            response = addinfourl(io.BytesIO(content), headers, request.full_url, code)
            response.msg = "Synthetic fixture response"
            return response

    def opener(*handlers):
        return build_opener(
            *[handler for handler in handlers if not isinstance(handler, HTTPSHandler)],
            SyntheticHTTPS(),
        )

    monkeypatch.setattr(downloads, "build_opener", opener)
    return visited


def policy(**kwargs):
    return downloads.DownloadPolicy(downloads.GITHUB_RELEASE_HOSTS, 100, 30, **kwargs)


@pytest.mark.parametrize(
    "destination",
    [
        "https://untrusted.example/private?secret=fixture",
        "http://github.com/downgrade",
        "https://name:fixture@github.com/private",
    ],
)
def test_redirect_is_rejected_before_an_untrusted_intermediate_can_be_requested(
    monkeypatch, destination: str
) -> None:
    start = "https://github.com/rtk-ai/rtk/releases/download/v1.2.3/asset"
    visited = scripted_https(
        monkeypatch, [destination, "https://release-assets.githubusercontent.com/final"]
    )
    with pytest.raises(downloads.DownloadError) as caught:
        downloads.read_https(start, policy())
    assert caught.value.kind == downloads.DownloadFailure.URL
    assert visited == [start]
    assert "fixture" not in str(caught.value) and "private" not in str(caught.value)


def test_allowed_https_redirects_preserve_payload_and_user_agent(monkeypatch) -> None:
    start = "https://github.com/rtk-ai/rtk/releases/download/v1.2.3/asset"
    redirects = ["https://github.com/pinned", "https://release-assets.githubusercontent.com/final"]
    visited = scripted_https(monkeypatch, redirects)
    assert downloads.read_https(start, policy()) == b"fixture"
    assert visited == [start, *redirects]


@pytest.mark.parametrize("size", [99, 100, 101])
def test_stream_is_bounded_and_only_a_complete_payload_is_returned(monkeypatch, size):
    scripted_https(monkeypatch, [], b"x" * size)
    if size > 100:
        with pytest.raises(downloads.DownloadError) as caught:
            downloads.read_https("https://github.com/asset", policy())
        assert caught.value.kind == downloads.DownloadFailure.SIZE
    else:
        assert downloads.read_https("https://github.com/asset", policy()) == b"x" * size


@pytest.mark.parametrize("problem", [403, 429, "network", "os", "value"])
def test_transport_error_keeps_only_typed_safe_diagnostics(monkeypatch, problem):
    secret = "SYNTHETIC_PRIVATE_VALUE"

    class Opener:
        def open(self, *args, **kwargs):
            if isinstance(problem, int):
                raise HTTPError(
                    "https://name:" + secret + "@github.com/private", problem, secret, {}, None
                )
            if problem == "network":
                raise URLError(secret)
            if problem == "os":
                raise OSError(secret)
            raise ValueError(secret)

    monkeypatch.setattr(downloads, "build_opener", lambda *handlers: Opener())
    with pytest.raises(downloads.DownloadError) as caught:
        downloads.read_https("https://github.com/private?token=" + secret, policy())
    assert secret not in str(caught.value) and "token=" not in str(caught.value)
    assert caught.value.host == "github.com"
    assert caught.value.status == (problem if isinstance(problem, int) else None)


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/asset",
        "https://github.com:0/asset",
        "https://github.com:444/asset",
        "https://user:password@github.com/asset",
        "https://github.com/private secret",
        "https://github.com/\nprivate",
        "https://unknown.example/asset",
    ],
)
def test_invalid_initial_origin_stops_before_opener_creation(monkeypatch, url):
    monkeypatch.setattr(downloads, "build_opener", lambda *args: pytest.fail("network started"))
    with pytest.raises(downloads.DownloadError):
        downloads.read_https(url, policy())


@pytest.mark.parametrize("proxy", ["http://remote.example:1234", "http://127.0.0.1:8910"])
def test_direct_policy_ignores_all_ambient_proxies(monkeypatch, proxy):
    monkeypatch.setenv("HTTP_PROXY", proxy)
    monkeypatch.setenv("HTTPS_PROXY", proxy)
    monkeypatch.setenv("ALL_PROXY", "socks://remote.example:1234")
    seen = []

    def opener(*handlers):
        seen.extend(h for h in handlers if isinstance(h, ProxyHandler))
        raise URLError("fixture stop before any socket")

    monkeypatch.setattr(downloads, "build_opener", opener)
    with pytest.raises(downloads.DownloadError):
        downloads.read_https("https://github.com/asset", policy())
    assert len(seen) == 1 and seen[0].proxies == {}


def test_elapsed_download_budget_fails_without_returning_partial_success(monkeypatch):
    scripted_https(monkeypatch, [], b"fixture")
    values = iter([0, 0, 31])
    monkeypatch.setattr(downloads.time, "monotonic", lambda: next(values))
    with pytest.raises(downloads.DownloadError) as caught:
        downloads.read_https("https://github.com/asset", policy(total_timeout=30))
    assert caught.value.kind == downloads.DownloadFailure.NETWORK


def test_artifact_total_budget_is_separate_from_per_socket_timeout(monkeypatch):
    scripted_https(monkeypatch, [], b"fixture")
    values = iter([0, 90, 91, 91, 92])
    monkeypatch.setattr(downloads.time, "monotonic", lambda: next(values))
    assert downloads.read_https("https://github.com/asset", policy()) == b"fixture"
    assert policy().timeout == 30 and policy().total_timeout == 600


@pytest.mark.parametrize("total", [True, 0, -1, float("inf"), float("nan")])
def test_total_download_budget_must_be_finite_and_positive(total):
    with pytest.raises(ValueError):
        policy(total_timeout=total)


@pytest.mark.parametrize("maximum,timeout", [(0, 30), (-1, 30), (100, 0), (100, float("inf"))])
def test_policy_requires_finite_positive_bounds(maximum, timeout):
    with pytest.raises(ValueError):
        downloads.DownloadPolicy(downloads.GITHUB_RELEASE_HOSTS, maximum, timeout)


@pytest.mark.parametrize("status", [204, 206])
def test_noncomplete_success_response_is_rejected_before_output(monkeypatch, status):
    scripted_https(monkeypatch, [], b"partial fixture", status=status)
    with pytest.raises(downloads.DownloadError) as caught:
        downloads.read_https("https://github.com/private?token=fixture", policy())
    assert caught.value.kind == downloads.DownloadFailure.HTTP
    assert caught.value.status == status and caught.value.host == "github.com"
    assert "token=" not in str(caught.value) and "fixture" not in str(caught.value)
