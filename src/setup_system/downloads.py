"""Bounded HTTPS transport with explicit origins, redirects and proxy selection."""

from __future__ import annotations

import hashlib
import io
import ipaddress
import math
import ssl
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from http.client import HTTPException
from typing import Any, BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

USER_AGENT = "setupSystem"
GITHUB_RELEASE_HOSTS = frozenset(
    {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
)
UV_RELEASE_HOSTS = GITHUB_RELEASE_HOSTS | {"releases.astral.sh"}
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


def validate_https_url(url: str, allowed_hosts: frozenset[str] | None = None) -> str:
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
            or parts.port == 0
            or (allowed_hosts is not None and host not in allowed_hosts)
        ):
            raise ValueError
    except (TypeError, ValueError):
        raise DownloadError(DownloadFailure.URL) from None
    return host


@dataclass(frozen=True)
class ProxyPolicy:
    """A direct connection or one credential-free HTTP listener on a loopback address."""

    url: str | None = None

    def __post_init__(self) -> None:
        if self.url is None:
            return
        try:
            parts = urlsplit(self.url)
            if (
                parts.scheme != "http"
                or parts.hostname is None
                or not ipaddress.ip_address(parts.hostname).is_loopback
                or parts.port is None
                or parts.port == 0
                or parts.username is not None
                or parts.password is not None
                or parts.path not in {"", "/"}
                or parts.query
                or parts.fragment
                or any(c.isspace() or ord(c) < 32 for c in self.url)
            ):
                raise ValueError
        except (TypeError, ValueError):
            raise DownloadError(DownloadFailure.URL) from None

    @classmethod
    def from_loopback_environment(cls, environment: Mapping[str, str]) -> ProxyPolicy:
        """Accept the worker's matching loopback pair; ignore unrelated ambient proxies."""
        http, https = environment.get("HTTP_PROXY"), environment.get("HTTPS_PROXY")
        if not http or http != https:
            return cls()
        try:
            return cls(http)
        except DownloadError:
            return cls()

    def handlers(self) -> dict[str, str]:
        return {} if self.url is None else {"http": self.url, "https": self.url}


class _LoopbackProxyHandler(ProxyHandler):
    """Honor an explicit worker proxy without consulting ambient NO_PROXY bypasses."""

    def proxy_open(self, req: Request, proxy: str, type: str) -> None:
        req.set_proxy(urlsplit(proxy).netloc, "http")


@dataclass(frozen=True)
class DownloadPolicy:
    allowed_hosts: frozenset[str] | None
    max_bytes: int
    timeout: float
    proxy: ProxyPolicy = ProxyPolicy()
    expose_host: bool = True
    total_timeout: float = 600

    def __post_init__(self) -> None:
        if (
            type(self.max_bytes) is not int
            or self.max_bytes <= 0
            or not math.isfinite(self.timeout)
            or self.timeout <= 0
            or not math.isfinite(self.total_timeout)
            or self.total_timeout <= 0
        ):
            raise ValueError("Download bounds must be finite and positive")
        if self.allowed_hosts is not None and (
            not self.allowed_hosts
            or any(validate_https_url("https://" + host) != host for host in self.allowed_hosts)
        ):
            raise ValueError("Download origins must be explicit HTTPS hostnames")


class HTTPSRedirects(HTTPRedirectHandler):
    """Reject each redirect before urllib can request an unapproved next origin."""

    def __init__(self, policy: DownloadPolicy) -> None:
        super().__init__()
        self.policy = policy

    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Any:
        validate_https_url(newurl, self.policy.allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class DownloadResult:
    size: int
    sha256: str


def copy_https(url: str, output: BinaryIO, policy: DownloadPolicy) -> DownloadResult:
    """Stream an approved response into an already owned destination within fixed bounds."""
    origin = validate_https_url(url, policy.allowed_hosts)
    safe_host = origin if policy.expose_host else None
    handler = (
        ProxyHandler({})
        if policy.proxy.url is None
        else _LoopbackProxyHandler(policy.proxy.handlers())
    )
    digest = hashlib.sha256()
    total = 0
    deadline = time.monotonic() + policy.total_timeout
    try:
        opener = build_opener(
            handler,
            HTTPSRedirects(policy),
            HTTPSHandler(context=ssl.create_default_context()),
        )
        with opener.open(
            Request(url, headers={"User-Agent": USER_AGENT}), timeout=policy.timeout
        ) as source:
            final = validate_https_url(source.geturl(), policy.allowed_hosts)
            safe_host = final if policy.expose_host else None
            if source.status != 200:
                raise DownloadError(DownloadFailure.HTTP, host=safe_host, status=source.status)
            while True:
                if time.monotonic() >= deadline:
                    raise DownloadError(DownloadFailure.NETWORK, host=safe_host)
                block = source.read(min(CHUNK_BYTES, policy.max_bytes - total + 1))
                if time.monotonic() >= deadline:
                    raise DownloadError(DownloadFailure.NETWORK, host=safe_host)
                if not block:
                    break
                total += len(block)
                if total > policy.max_bytes:
                    raise DownloadError(DownloadFailure.SIZE, host=safe_host)
                output.write(block)
                digest.update(block)
    except HTTPError as exc:
        try:
            final = validate_https_url(exc.url, policy.allowed_hosts)
            safe_host = final if policy.expose_host else None
        except DownloadError:
            pass
        exc.close()
        raise DownloadError(DownloadFailure.HTTP, host=safe_host, status=exc.code) from None
    except (URLError, OSError, ValueError, HTTPException):
        raise DownloadError(DownloadFailure.NETWORK, host=safe_host) from None
    return DownloadResult(total, digest.hexdigest())


def read_https(url: str, policy: DownloadPolicy) -> bytes:
    output = io.BytesIO()
    copy_https(url, output, policy)
    return output.getvalue()
