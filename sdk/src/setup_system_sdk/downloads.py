"""Read bounded HTTPS responses from explicitly allowed public origins."""

from __future__ import annotations

import hashlib
import hmac
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urljoin, urlsplit

import anyio
import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

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
    DIGEST = "digest"


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
            DownloadFailure.DIGEST: "response digest differs from the approved release",
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


def _temporary(error: BaseException) -> bool:
    return isinstance(
        error, (httpx.TimeoutException, httpx.ReadError, httpx.RemoteProtocolError)
    ) or (isinstance(error, DownloadError) and error.status in {408, 429, 502, 503, 504})


class DownloadClient:
    """Reuse an anonymous HTTP transport for bounded idempotent reads."""

    def __init__(self, client: httpx.AsyncClient, budget: int = 256 * 1024 * 1024) -> None:
        if budget < 1:
            raise ValueError("Download budget must be positive")
        self.client = client
        self.budget = budget
        self._available = budget
        self._condition = anyio.Condition()

    async def read(self, url: str, policy: DownloadPolicy, digest: str | None = None) -> bytes:
        if digest is not None and not re.fullmatch("[a-f0-9]{64}", digest):
            raise ValueError("Invalid expected SHA-256")
        if policy.max_bytes > self.budget:
            raise DownloadError(DownloadFailure.SIZE)
        host = validate_https_url(url, policy.allowed_hosts)
        try:
            with anyio.fail_after(policy.total_timeout):
                async with self._condition:
                    while self._available < policy.max_bytes:
                        await self._condition.wait()
                    self._available -= policy.max_bytes
                try:
                    async for attempt in AsyncRetrying(
                        retry=retry_if_exception(_temporary),
                        stop=stop_after_attempt(3),
                        wait=wait_exponential(multiplier=0.2, max=2),
                        reraise=True,
                    ):
                        with attempt:
                            return await self._read(url, policy, digest)
                finally:
                    with anyio.CancelScope(shield=True):
                        async with self._condition:
                            self._available += policy.max_bytes
                            self._condition.notify_all()
        except (httpx.HTTPError, OSError, TimeoutError):
            raise DownloadError(DownloadFailure.NETWORK, host=host) from None
        raise RuntimeError("Download retry finished without a response")

    async def _read(self, url: str, policy: DownloadPolicy, digest: str | None) -> bytes:
        for _ in range(11):
            host = validate_https_url(url, policy.allowed_hosts)
            # Construct each request without client credentials, cookies or auth headers.
            request = httpx.Request(
                "GET",
                url,
                headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
                extensions={"timeout": httpx.Timeout(policy.timeout).as_dict()},
            )
            response = await self.client.send(
                request, stream=True, follow_redirects=False, auth=None
            )
            try:
                if response.is_redirect and policy.follow_redirects:
                    location = response.headers.get("Location")
                    if not location:
                        raise DownloadError(DownloadFailure.URL)
                    url = urljoin(url, location)
                    continue
                if response.status_code != 200:
                    raise DownloadError(
                        DownloadFailure.HTTP, host=host, status=response.status_code
                    )
                data = bytearray()
                async for block in response.aiter_raw(min(CHUNK_BYTES, policy.max_bytes + 1)):
                    if len(data) + len(block) > policy.max_bytes:
                        raise DownloadError(DownloadFailure.SIZE, host=host)
                    data.extend(block)
                if digest and not hmac.compare_digest(hashlib.sha256(data).hexdigest(), digest):
                    raise DownloadError(DownloadFailure.DIGEST, host=host)
                return bytes(data)
            finally:
                with anyio.CancelScope(shield=True):
                    await response.aclose()
        raise DownloadError(DownloadFailure.URL)


async def download_https(url: str, policy: DownloadPolicy) -> bytes:
    async with httpx.AsyncClient(trust_env=False) as client:
        return await DownloadClient(client, policy.max_bytes).read(url, policy)


def read_https(url: str, policy: DownloadPolicy) -> bytes:
    """Read from a synchronous worker tool or standalone process."""
    try:
        anyio.from_thread.check_cancelled()
    except RuntimeError:
        return anyio.run(download_https, url, policy, backend="asyncio")
    return anyio.from_thread.run(download_https, url, policy)
