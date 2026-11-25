"""Read bounded HTTPS responses from explicitly allowed public origins."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urljoin, urlsplit

import httpx

USER_AGENT = "setupSystem"
GITHUB_RELEASE_HOSTS = frozenset(
    {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
)
CHUNK_BYTES = 1024 * 1024


class DownloadFailure(StrEnum):
    URL = "url"
    HTTP = "http"
    NETWORK = "network"
    SIZE = "size"


class DownloadError(RuntimeError):
    """Transport diagnostics retain only an approved origin and numeric HTTP status."""

    def __init__(
        self, kind: DownloadFailure, *, host: str | None = None, status: int | None = None
    ) -> None:
        self.kind = kind
        self.host = host
        self.status = status
        detail = {
            DownloadFailure.URL: "HTTPS origin or redirect is not allowed",
            DownloadFailure.HTTP: f"HTTP {status}",
            DownloadFailure.NETWORK: "network or certificate failure",
            DownloadFailure.SIZE: "response exceeds its size limit",
        }[kind]
        super().__init__(detail + (f" from {host}" if host else ""))


def validate_https_url(url: str, allowed_hosts: frozenset[str]) -> str:
    """Return the HTTPS hostname without retaining a path, query or credentials."""
    try:
        if not isinstance(url, str) or not 0 < len(url) <= 16384:
            raise ValueError
        if any(c.isspace() or ord(c) < 32 or 127 <= ord(c) < 160 for c in url):
            raise ValueError
        parts = urlsplit(url)
        host = parts.hostname
        if (
            parts.scheme != "https"
            or not host
            or parts.username is not None
            or parts.password is not None
            or parts.port not in (None, 443)
            or host not in allowed_hosts
        ):
            raise ValueError
    except (TypeError, ValueError):
        raise DownloadError(DownloadFailure.URL) from None
    return host


@dataclass(frozen=True)
class DownloadPolicy:
    allowed_hosts: frozenset[str]
    max_bytes: int
    timeout: float
    total_timeout: float = 600
    follow_redirects: bool = True

    def __post_init__(self) -> None:
        if (
            type(self.max_bytes) is not int
            or self.max_bytes <= 0
            or isinstance(self.timeout, bool)
            or not math.isfinite(self.timeout)
            or self.timeout <= 0
            or isinstance(self.total_timeout, bool)
            or not math.isfinite(self.total_timeout)
            or self.total_timeout <= 0
        ):
            raise ValueError("Download bounds must be finite and positive")
        if not self.allowed_hosts or any(
            not isinstance(host, str)
            or validate_https_url("https://" + host, self.allowed_hosts) != host
            for host in self.allowed_hosts
        ):
            raise ValueError("Download origins must be explicit HTTPS hostnames")


async def download_https(url: str, policy: DownloadPolicy) -> bytes:
    """Read a bounded response. Validate each redirect before making its request."""
    host = validate_https_url(url, policy.allowed_hosts)
    try:
        async with (
            asyncio.timeout(policy.total_timeout),
            httpx.AsyncClient(
                timeout=policy.timeout,
                trust_env=False,
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
            ) as client,
        ):
            for _ in range(11):
                async with client.stream("GET", url) as response:
                    if response.is_redirect and policy.follow_redirects:
                        location = response.headers.get("Location")
                        if not location:
                            raise DownloadError(DownloadFailure.URL)
                        url = urljoin(url, location)
                        host = validate_https_url(url, policy.allowed_hosts)
                        continue
                    if response.status_code != 200:
                        raise DownloadError(
                            DownloadFailure.HTTP, host=host, status=response.status_code
                        )
                    data = bytearray()
                    async for block in response.aiter_raw(
                        chunk_size=min(CHUNK_BYTES, policy.max_bytes + 1)
                    ):
                        if len(data) + len(block) > policy.max_bytes:
                            raise DownloadError(DownloadFailure.SIZE, host=host)
                        data.extend(block)
                    return bytes(data)
            raise DownloadError(DownloadFailure.URL)
    except (httpx.HTTPError, httpx.InvalidURL, OSError, ValueError, TimeoutError):
        pass
    raise DownloadError(DownloadFailure.NETWORK, host=host)


def read_https(url: str, policy: DownloadPolicy) -> bytes:
    """Synchronous entry for standalone Make and Ansible helper processes."""
    return asyncio.run(download_https(url, policy))
