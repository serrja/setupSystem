"""Unprivileged worker owns temporary client files, secret input and cleanup."""

from __future__ import annotations

import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit

from setup_system.installation import TargetUser, validate_user_path


def subscription_address(value: str) -> str:
    value = value.strip()
    if (
        not value
        or len(value) > 8192
        or any(ord(character) < 32 or 127 <= ord(character) < 160 for character in value)
    ):
        raise ValueError("Enter one bounded HTTPS subscription address")
    try:
        parts = urlsplit(value)
        valid = (
            parts.scheme == "https"
            and parts.hostname
            and parts.username is None
            and parts.password is None
        )
        _ = parts.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("A valid HTTPS subscription address is required")
    return value


def validate_subscription_file(path: Path, target: TargetUser) -> Path:
    candidate = validate_user_path(path, target)
    info = candidate.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or not 0 < info.st_size <= 8192
        or info.st_mode & 0o077
    ):
        raise ValueError("Subscription input must be one private, bounded regular user file")
    return candidate


def _subscription(path: Path | None, incoming: object, target: TargetUser) -> str:
    if path is not None:
        if incoming is not None:
            raise ValueError("Choose a private subscription file or hidden input")
        candidate = validate_subscription_file(path, target)
        descriptor = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if (
                info.st_uid != os.geteuid()
                or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or not 0 < info.st_size <= 8192
                or info.st_mode & 0o077
            ):
                raise ValueError("Subscription input must remain a private regular user file")
            incoming = stream.read(8193)
    if not isinstance(incoming, str):
        raise ValueError("A subscription address is required")
    return subscription_address(incoming)


def _emit(message: dict[str, object]) -> None:
    print(json.dumps(message), flush=True)


def serve_network(root: Path, target: TargetUser, subscription_file: Path | None) -> int:
    if os.geteuid() == 0 or os.geteuid() != target.uid:
        raise ValueError("Temporary network preparation requires the real non-root account")
    from setup_system.application.happ import download_happ
    from setup_system.network import temporary_proxy

    stopped = threading.Event()
    ready = threading.Event()
    control_ok = threading.Event()

    def cancel(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, cancel)
    stack = ExitStack()
    stage = "input"
    code = 0
    try:
        raw = sys.stdin.buffer.readline(65537)
        if len(raw) > 65536:
            raise ValueError("Temporary network request exceeds its limit")
        request = json.loads(raw)
        if (
            not isinstance(request, dict)
            or set(request) != {"schema", "operation", "subscription"}
            or request["schema"] != 1
            or request["operation"] != "start"
        ):
            raise ValueError("Invalid temporary network request")
        secret = _subscription(subscription_file, request["subscription"], target)

        def control() -> None:
            try:
                command = sys.stdin.buffer.readline(1025)
                if len(command) <= 1024 and json.loads(command) == {"operation": "stop"}:
                    control_ok.set()
            except (OSError, ValueError):
                pass
            finally:
                stopped.set()
                if not ready.is_set():
                    os.kill(os.getpid(), signal.SIGTERM)

        threading.Thread(target=control, daemon=True).start()
        _emit({"state": "progress", "stage": "temporary-client"})
        stage = "temporary-client"
        cache = validate_user_path(Path(os.environ["XDG_CACHE_HOME"]) / "setup-system", target)
        cache.mkdir(parents=True, exist_ok=True)
        artifact_dir = Path(
            stack.enter_context(tempfile.TemporaryDirectory(prefix="network-", dir=cache))
        )
        artifact_dir.chmod(0o700)
        proxy = stack.enter_context(temporary_proxy(root, secret, share_process_group=True))
        # Only this worker's outbound artifact traffic uses the temporary listener.
        for key in ("NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy"):
            os.environ.pop(key, None)
        os.environ.update(HTTP_PROXY=proxy.http_proxy, HTTPS_PROXY=proxy.http_proxy)
        stage = "happ-download"
        happ = download_happ(root, artifact_dir / "happ.deb")
        ready.set()
        _emit({"state": "ready", "proxy": proxy.http_proxy, "happ": str(happ)})
        stage = "downloads"
        if not stopped.wait(7200) or not control_ok.is_set():
            raise RuntimeError("Temporary network owner disconnected or exceeded its lifetime")
    except (
        OSError,
        ValueError,
        RuntimeError,
        subprocess.SubprocessError,
        KeyboardInterrupt,
    ) as exc:
        # Only a status number is retained; provider/engine messages may contain credentials.
        match = re.search(r"\bHTTP\s*([1-5][0-9]{2})\b", str(exc))
        _emit({"state": "failed", "stage": stage, "http_status": int(match[1]) if match else None})
        code = 5
    finally:
        try:
            stack.close()
        except (OSError, RuntimeError, KeyboardInterrupt):
            _emit({"state": "failed", "stage": "cleanup", "http_status": None})
            code = 5
        else:
            _emit({"state": "closed"})
    return code
