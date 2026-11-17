"""Ephemeral, unprivileged Xray HTTP proxy for explicitly authorized downloads.

Only a secure VLESS outbound is selected from provider data. Provider listeners,
routes, file paths, DNS, executable hooks and logging settings are never applied.
A listening proxy does not establish that the upstream server is reachable.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import ipaddress
import json
import os
import platform
import re
import signal
import socket
import stat
import subprocess
import tempfile
import threading
import time
import tomllib
import uuid
import zipfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from setup_system.capabilities import load_policy
from setup_system.downloads import (
    GITHUB_RELEASE_HOSTS,
    DownloadError,
    DownloadFailure,
    DownloadPolicy,
    read_https,
    validate_https_url,
)

MAX_SUBSCRIPTION = 2 * 1024 * 1024
MAX_ARCHIVE = 32 * 1024 * 1024
MAX_BINARY = 64 * 1024 * 1024
ASSET_HOSTS = GITHUB_RELEASE_HOSTS


class NetworkError(RuntimeError):
    """An actionable failure whose message does not disclose provider data."""


@dataclass(frozen=True)
class ProxyDetails:
    http_proxy: str
    session_dir: Path


def _text(value: object, *, limit: int = 2048) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in value)
    ):
        raise NetworkError("Unsupported subscription field")
    return value


def _https(url: str, hosts: frozenset[str] | None = None) -> str:
    try:
        return validate_https_url(url, hosts)
    except DownloadError:
        raise NetworkError(
            "A valid HTTPS URL without embedded account credentials is required"
        ) from None


def _fetch(url: str, limit: int, *, asset: bool = False) -> bytes:
    policy = DownloadPolicy(
        ASSET_HOSTS if asset else None,
        limit,
        30,
        expose_host=False,
        total_timeout=600 if asset else 30,
    )
    try:
        return read_https(url, policy)
    except DownloadError as exc:
        if exc.kind == DownloadFailure.HTTP:
            purpose = "Temporary engine download" if asset else "Subscription download"
            raise NetworkError(
                f"{purpose}: HTTP {exc.status}; check the initial network connection"
            ) from None
        if exc.kind == DownloadFailure.SIZE:
            raise NetworkError("HTTPS response exceeds the download size limit") from None
        raise NetworkError(
            "HTTPS download failed; check the initial network connection and certificate trust"
        ) from None


def _address(value: object) -> str:
    address = _text(value, limit=253)
    try:
        return str(ipaddress.ip_address(address))
    except ValueError:
        if (
            not address
            or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", address)
            or ".." in address
        ):
            raise NetworkError("Unsupported VLESS server address") from None
        return address


def _vless(
    address: object, port: object, identity: object, query: dict[str, str]
) -> dict[str, Any]:
    server = _address(address)
    if isinstance(port, bool):
        raise NetworkError("Unsupported VLESS port")
    try:
        number = int(port) if isinstance(port, (str, int)) else 0
        identifier = str(uuid.UUID(_text(identity, limit=64)))
    except (ValueError, TypeError, AttributeError):
        raise NetworkError("VLESS requires a valid port and UUID") from None
    if not 1 <= number <= 65535:
        raise NetworkError("Unsupported VLESS port")
    if query.get("encryption", "none") != "none":
        raise NetworkError(
            "This temporary client supports VLESS encryption=none with TLS or REALITY"
        )
    for name in ("allowInsecure", "insecure", "skip-cert-verify"):
        if query.get(name, "0").lower() not in {"0", "false"}:
            raise NetworkError("Insecure TLS is not permitted")
    transport = query.get("type", "tcp")
    transport = "raw" if transport == "tcp" else transport
    if transport not in {"raw", "ws", "grpc"} or query.get("headerType", "none") != "none":
        raise NetworkError("Unsupported VLESS transport; supported: TCP, WebSocket and gRPC")
    security = query.get("security", "")
    if security not in {"tls", "reality"}:
        raise NetworkError("The temporary VLESS connection requires TLS or REALITY")
    if security == "reality" and transport == "ws":
        raise NetworkError("Pinned Xray does not support REALITY over WebSocket")
    flow = query.get("flow", "")
    if flow not in {"", "xtls-rprx-vision"} or (flow and transport != "raw"):
        raise NetworkError("Unsupported VLESS flow/transport combination")
    stream: dict[str, Any] = {"network": transport, "security": security}
    server_name = _address(query.get("sni") or server)
    fingerprint = _text(query.get("fp", "chrome"), limit=64)
    if fingerprint not in {
        "chrome",
        "firefox",
        "safari",
        "ios",
        "android",
        "edge",
        "360",
        "qq",
        "random",
        "randomized",
    }:
        raise NetworkError("Unsupported TLS fingerprint")
    if security == "tls":
        tls: dict[str, Any] = {
            "serverName": server_name,
            "allowInsecure": False,
            "fingerprint": fingerprint,
        }
        if query.get("alpn"):
            alpn = query["alpn"].split(",")
            if len(alpn) > 8 or any(
                not re.fullmatch(r"[A-Za-z0-9./_-]{1,64}", item) for item in alpn
            ):
                raise NetworkError("Unsupported TLS ALPN")
            tls["alpn"] = alpn
        stream["tlsSettings"] = tls
    else:
        key = _text(query.get("pbk", ""), limit=64)
        try:
            if (
                "=" in key
                or len(base64.b64decode(key + "=" * (-len(key) % 4), altchars=b"-_", validate=True))
                != 32
            ):
                raise ValueError
        except (ValueError, binascii.Error):
            raise NetworkError("REALITY requires a valid provider public key") from None
        short_id = query.get("sid", "")
        if not re.fullmatch(r"(?:[0-9a-fA-F]{2}){0,8}", short_id):
            raise NetworkError("Unsupported REALITY short ID")
        reality = {
            "serverName": server_name,
            "fingerprint": fingerprint,
            "publicKey": key,
            "shortId": short_id,
        }
        if query.get("spx"):
            path = _text(query["spx"])
            if not path.startswith("/"):
                raise NetworkError("Unsupported REALITY spider path")
            reality["spiderX"] = path
        stream["realitySettings"] = reality
    if transport == "ws":
        path = _text(query.get("path", "/"))
        if not path.startswith("/"):
            raise NetworkError("Unsupported WebSocket path")
        websocket: dict[str, Any] = {"path": path}
        if query.get("host"):
            websocket["host"] = _address(query["host"])
        stream["wsSettings"] = websocket
    if transport == "grpc":
        mode = query.get("mode", "gun")
        if mode not in {"gun", "multi"}:
            raise NetworkError("Unsupported gRPC mode")
        grpc: dict[str, Any] = {
            "serviceName": _text(query.get("serviceName", "")),
            "multiMode": mode == "multi",
        }
        if query.get("authority"):
            grpc["authority"] = _address(query["authority"])
        stream["grpcSettings"] = grpc
    settings: dict[str, Any] = {
        "address": server,
        "port": number,
        "id": identifier,
        "encryption": "none",
    }
    if flow:
        settings["flow"] = flow
    return {
        "tag": "download-proxy",
        "protocol": "vless",
        "settings": settings,
        "streamSettings": stream,
    }


def _uri(value: str) -> dict[str, Any]:
    try:
        parts = urlsplit(value)
        pairs = parse_qsl(
            parts.query, keep_blank_values=True, strict_parsing=True, max_num_fields=32
        )
        query = dict(pairs)
        if (
            parts.scheme != "vless"
            or parts.password
            or not parts.username
            or parts.path not in {"", "/"}
            or len(query) != len(pairs)
        ):
            raise ValueError
        allowed = {
            "encryption",
            "security",
            "type",
            "sni",
            "fp",
            "pbk",
            "sid",
            "spx",
            "alpn",
            "flow",
            "host",
            "path",
            "serviceName",
            "mode",
            "authority",
            "headerType",
            "allowInsecure",
            "insecure",
            "skip-cert-verify",
        }
        if set(query) - allowed:
            raise NetworkError("The VLESS URI contains unsupported parameters")
        query = {name: _text(item) for name, item in query.items()}
        return _vless(parts.hostname, parts.port, unquote(parts.username), query)
    except (ValueError, TypeError):
        raise NetworkError("Invalid VLESS subscription entry") from None


def _native(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("protocol") != "vless":
        raise NetworkError("Only VLESS outbounds are supported by the temporary client")
    if set(value) - {"tag", "protocol", "settings", "streamSettings"}:
        raise NetworkError("Native outbound contains unsupported connection features")
    settings, stream = value.get("settings"), value.get("streamSettings", {})
    if not isinstance(settings, dict) or not isinstance(stream, dict):
        raise NetworkError("Invalid native VLESS configuration")
    if "vnext" in settings:
        servers = settings["vnext"]
        if not isinstance(servers, list) or len(servers) != 1 or not isinstance(servers[0], dict):
            raise NetworkError("Native VLESS needs exactly one server")
        users = servers[0].get("users")
        if not isinstance(users, list) or len(users) != 1 or not isinstance(users[0], dict):
            raise NetworkError("Native VLESS needs exactly one user")
        user = users[0]
        address, port = servers[0].get("address"), servers[0].get("port")
    else:
        user = settings
        address, port = settings.get("address"), settings.get("port")
    if set(user) - {"address", "port", "id", "encryption", "flow", "level", "email"}:
        raise NetworkError("Native VLESS user contains unsupported features")
    if set(stream) - {
        "network",
        "method",
        "security",
        "tlsSettings",
        "realitySettings",
        "wsSettings",
        "grpcSettings",
        "tcpSettings",
        "rawSettings",
    }:
        raise NetworkError("Native stream contains unsupported features")
    if stream.get("tcpSettings") or stream.get("rawSettings"):
        raise NetworkError("Custom TCP headers are unsupported")
    query = {
        "type": stream.get("network", stream.get("method", "tcp")),
        "security": stream.get("security", ""),
        "encryption": user.get("encryption", "none"),
        "flow": user.get("flow", ""),
    }
    security = query["security"]
    secure = stream.get("tlsSettings" if security == "tls" else "realitySettings", {})
    if not isinstance(secure, dict) or set(secure) - {
        "serverName",
        "fingerprint",
        "alpn",
        "allowInsecure",
        "publicKey",
        "password",
        "shortId",
        "spiderX",
        "show",
    }:
        raise NetworkError("Native TLS settings contain unsupported features or file paths")
    if secure.get("allowInsecure") not in {None, False} or secure.get("show") not in {None, False}:
        raise NetworkError("Insecure TLS and provider debug logging are not permitted")
    for name, field in (
        ("sni", "serverName"),
        ("fp", "fingerprint"),
        ("sid", "shortId"),
        ("spx", "spiderX"),
    ):
        if field in secure:
            query[name] = secure[field]
    if security == "reality":
        query["pbk"] = secure.get("password") or secure.get("publicKey", "")
    if "alpn" in secure:
        alpn = secure["alpn"]
        if not isinstance(alpn, list) or any(not isinstance(item, str) for item in alpn):
            raise NetworkError("Invalid native TLS ALPN")
        query["alpn"] = ",".join(alpn)
    if query["type"] == "ws":
        ws = stream.get("wsSettings", {})
        if not isinstance(ws, dict) or set(ws) - {"host", "path", "headers"}:
            raise NetworkError("Unsupported native WebSocket settings")
        headers = ws.get("headers", {})
        if not isinstance(headers, dict) or set(headers) - {"Host"}:
            raise NetworkError("Unsupported native WebSocket headers")
        query.update(path=ws.get("path", "/"), host=ws.get("host") or headers.get("Host", ""))
    if query["type"] == "grpc":
        grpc = stream.get("grpcSettings", {})
        if (
            not isinstance(grpc, dict)
            or set(grpc) - {"serviceName", "authority", "multiMode"}
            or type(grpc.get("multiMode", False)) is not bool
        ):
            raise NetworkError("Unsupported native gRPC settings")
        query.update(
            serviceName=grpc.get("serviceName", ""),
            authority=grpc.get("authority", ""),
            mode="multi" if grpc.get("multiMode") else "gun",
        )
    return _vless(
        address, port, user.get("id"), {name: _text(item) for name, item in query.items()}
    )


def parse_subscription(data: bytes) -> dict[str, Any]:
    """Select the first secure VLESS node, without applying provider program settings."""
    if not data or len(data) > MAX_SUBSCRIPTION:
        raise NetworkError("Subscription is empty or exceeds its size limit")
    try:
        text = data.decode("utf-8-sig").strip()
        if text.startswith(("{", "[")):
            document = json.loads(text)
            documents = document if isinstance(document, list) else [document]
            for item in documents:
                if not isinstance(item, dict):
                    continue
                candidates = item.get("outbounds", [item])
                if isinstance(candidates, list):
                    for outbound in candidates:
                        if isinstance(outbound, dict) and outbound.get("protocol") == "vless":
                            try:
                                return _native(outbound)
                            except NetworkError:
                                continue
            raise NetworkError("No supported secure VLESS outbound in native JSON")
        if re.search(r"(?m)^[ \t]*[A-Za-z][A-Za-z0-9+.-]*://", text) is None:
            packed = "".join(text.split()).encode("ascii")
            text = base64.b64decode(
                packed + b"=" * (-len(packed) % 4), altchars=b"-_", validate=True
            ).decode("utf-8")
        lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if not lines or len(lines) > 512:
            raise NetworkError("Subscription node count is empty or exceeds its limit")
        for line in lines:
            if line.startswith("vless://"):
                try:
                    return _uri(line)
                except NetworkError:
                    continue
        raise NetworkError("No supported secure VLESS node; use native Xray JSON or a URI list")
    except (ValueError, UnicodeError, RecursionError, TypeError, AttributeError):
        raise NetworkError(
            "Unsupported subscription; encrypted Happ links, HTML "
            "and unknown formats are not imported"
        ) from None


def proxy_config(outbound: dict[str, Any], port: int) -> dict[str, Any]:
    if not 1024 <= port <= 65535:
        raise NetworkError("Temporary proxy requires a high loopback port")
    return {
        "log": {"access": "none", "error": "none", "logLevel": "none", "dnsLog": False},
        "inbounds": [
            {
                "tag": "installer",
                "listen": "127.0.0.1",
                "port": port,
                "protocol": "http",
                "settings": {"allowTransparent": False},
            }
        ],
        "outbounds": [outbound],
    }


@dataclass(frozen=True)
class EngineAsset:
    name: str
    sha256: str


@dataclass(frozen=True)
class EnginePolicy:
    version: str
    source: str
    assets: Mapping[str, EngineAsset]


def _engine_policy(root: Path) -> EnginePolicy:
    path = root / "manifests/network.toml"
    try:
        details = path.lstat()
        if not stat.S_ISREG(details.st_mode) or not 0 < details.st_size <= 16384:
            raise ValueError
        manifest = tomllib.loads(path.read_text())
        version, source = manifest.get("version"), manifest.get("source")
        linux = manifest.get("linux")
        if (
            not isinstance(version, str)
            or re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}", version) is None
            or not isinstance(source, str)
            or re.fullmatch(r"https://github\.com/XTLS/Xray-core/tree/[0-9a-f]{40}", source) is None
            or not isinstance(linux, dict)
            or not linux
        ):
            raise ValueError
        capabilities = load_policy(root)
        assets: dict[str, EngineAsset] = {}
        for architecture, entry in linux.items():
            if capabilities.debian_architecture(architecture) != architecture:
                raise ValueError
            if not isinstance(entry, dict):
                raise ValueError
            name, digest = entry.get("asset"), entry.get("sha256")
            if (
                not isinstance(name, str)
                or re.fullmatch(r"Xray-linux-[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\.zip", name) is None
                or ".." in name
                or not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            ):
                raise ValueError
            assets[architecture] = EngineAsset(name, digest)
    except (OSError, TypeError, ValueError, KeyError):
        raise NetworkError("Invalid temporary engine manifest or pin") from None
    return EnginePolicy(version, source, MappingProxyType(assets))


def _engine(root: Path, directory: Path) -> Path:
    policy = _engine_policy(root)
    try:
        arch = load_policy(root).debian_architecture(platform.machine())
        if platform.system() != "Linux":
            raise ValueError
        entry = policy.assets[arch]
    except (ValueError, KeyError):
        raise NetworkError("Temporary network engine supports pinned Linux targets only") from None
    version, asset, expected = policy.version, entry.name, entry.sha256
    seed = root / "downloads" / f"xray-v{version}-{arch}.zip"
    archive = b""
    if seed.is_file() and not seed.is_symlink() and seed.stat().st_size <= MAX_ARCHIVE:
        candidate = seed.read_bytes()
        if hashlib.sha256(candidate).hexdigest() == expected:
            archive = candidate
    if not archive:
        archive = _fetch(
            f"https://github.com/XTLS/Xray-core/releases/download/v{version}/{asset}",
            MAX_ARCHIVE,
            asset=True,
        )
    if hashlib.sha256(archive).hexdigest() != expected:
        raise NetworkError("Temporary engine archive SHA-256 mismatch")
    archive_path = directory / "engine.zip"
    archive_path.write_bytes(archive)
    archive_path.chmod(0o600)
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            entries = bundle.infolist()
            if len(entries) > 64 or sum(item.file_size for item in entries) > 96 * 1024 * 1024:
                raise NetworkError("Temporary engine archive exceeds extraction limits")
            names: set[str] = set()
            for item in entries:
                path = Path(item.filename)
                kind = stat.S_IFMT(item.external_attr >> 16)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in item.filename
                    or item.filename in names
                    or kind not in {0, stat.S_IFREG, stat.S_IFDIR}
                ):
                    raise NetworkError("Temporary engine archive contains unsafe entries")
                names.add(item.filename)
            binaries = [item for item in entries if item.filename == "xray" and not item.is_dir()]
            if (
                len(binaries) != 1
                or not 0 < binaries[0].file_size <= MAX_BINARY
                or stat.S_IFMT(binaries[0].external_attr >> 16) not in {0, stat.S_IFREG}
            ):
                raise NetworkError("Temporary archive must contain one bounded regular xray binary")
            executable = directory / "xray"
            with bundle.open(binaries[0]) as source, executable.open("xb") as destination:
                destination.write(source.read(MAX_BINARY + 1))
            executable.chmod(0o700)
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        if isinstance(exc, NetworkError):
            raise
        raise NetworkError("Temporary engine archive could not be verified") from None
    return executable


def _stop(process: subprocess.Popen[bytes], *, own_group: bool = True) -> None:
    if not own_group:
        # The privileged controller owns the surrounding worker process group.
        # Never signal that group from its worker: only reap our direct engine.
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    # A leader can exit while a descendant ignores TERM. Clean the whole owned group.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=3)


@contextmanager
def temporary_proxy(
    root: Path, secret: str, *, share_process_group: bool = False
) -> Iterator[ProxyDetails]:
    """Own one loopback proxy session and remove its files/process on every normal exit."""
    if os.geteuid() == 0:
        raise NetworkError("The temporary network engine must run as the verified non-root user")
    _https(secret)
    outbound = parse_subscription(_fetch(secret, MAX_SUBSCRIPTION))
    process: subprocess.Popen[bytes] | None = None
    previous: Any = None

    def interrupted(signum: int, frame: Any) -> None:
        raise NetworkError("Temporary network session interrupted")

    with tempfile.TemporaryDirectory(prefix="setup-system-network-") as name:
        directory = Path(name)
        directory.chmod(0o700)
        try:
            if threading.current_thread() is threading.main_thread():
                previous = signal.signal(signal.SIGTERM, interrupted)
            executable = _engine(root, directory)
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            config = directory / "config.json"
            descriptor = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                json.dump(proxy_config(outbound, port), stream)
            environment = {
                "PATH": "/usr/bin:/bin",
                "HOME": str(directory),
                "LANG": "C",
                "LC_ALL": "C",
            }
            # Xray errors may repeat credentials or public keys: discard both streams.
            tested = subprocess.run(
                [str(executable), "run", "-test", "-format=json", "-c", str(config)],
                cwd=directory,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
            )
            if tested.returncode:
                raise NetworkError(
                    "The pinned temporary engine rejected the selected node configuration"
                )
            process = subprocess.Popen(
                [str(executable), "run", "-format=json", "-c", str(config)],
                cwd=directory,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=not share_process_group,
            )
            deadline = time.monotonic() + 10
            while True:
                if process.poll() is not None:
                    raise NetworkError("Temporary proxy process exited before readiness")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        pass
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise NetworkError(
                            "Temporary proxy did not become ready before timeout"
                        ) from None
                    time.sleep(0.05)
            yield ProxyDetails(f"http://127.0.0.1:{port}", directory)
        except (OSError, subprocess.SubprocessError):
            raise NetworkError(
                "Temporary network engine failed; no provider details were logged"
            ) from None
        finally:
            try:
                if process is not None:
                    _stop(process, own_group=not share_process_group)
            finally:
                if previous is not None:
                    signal.signal(signal.SIGTERM, previous)
