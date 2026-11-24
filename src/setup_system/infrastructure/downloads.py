"""Read bounded HTTPS responses from explicitly allowed public origins."""

from __future__ import annotations

import io
import math
import ssl
import time
from dataclasses import dataclass
from enum import StrEnum
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

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


def read_https(url: str, policy: DownloadPolicy) -> bytes:
    """Return only a complete response within the per-source budget."""
    safe_host = validate_https_url(url, policy.allowed_hosts)
    output = io.BytesIO()
    total = 0
    deadline = time.monotonic() + policy.total_timeout
    try:
        opener = build_opener(
            ProxyHandler({}),
            HTTPSRedirects(policy),
            HTTPSHandler(context=ssl.create_default_context()),
        )
        with opener.open(
            Request(url, headers={"User-Agent": USER_AGENT}), timeout=policy.timeout
        ) as source:
            safe_host = validate_https_url(source.geturl(), policy.allowed_hosts)
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
    except HTTPError as exc:
        try:
            safe_host = validate_https_url(exc.url, policy.allowed_hosts)
        except DownloadError:
            pass
        exc.close()
        raise DownloadError(DownloadFailure.HTTP, host=safe_host, status=exc.code) from None
    except (URLError, OSError, ValueError, HTTPException):
        raise DownloadError(DownloadFailure.NETWORK, host=safe_host) from None
    return output.getvalue()
