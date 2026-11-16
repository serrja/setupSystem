"""Privileged-side lifecycle adapter for an unprivileged temporary download proxy."""

from __future__ import annotations

import contextlib
import json
import os
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from setup_system.installation import TargetUser


@dataclass(frozen=True)
class NetworkReady:
    http_proxy: str
    happ: Path


class TemporaryNetwork:
    """Keep secrets on stdin and trust only a bounded, validated worker acknowledgement."""

    def __init__(
        self,
        root: Path,
        target: TargetUser,
        environment: dict[str, str],
        *,
        subscription_file: Path | None = None,
        factory: Any = subprocess.Popen,
        startup_timeout: float = 1200,
        shutdown_timeout: float = 20,
    ) -> None:
        self.root = root
        self.target = target
        self.environment = environment
        self.subscription_file = subscription_file
        self.factory = factory
        self.startup_timeout = startup_timeout
        self.shutdown_timeout = shutdown_timeout
        self.process: subprocess.Popen[bytes] | None = None
        self.buffer = bytearray()
        self.ready: NetworkReady | None = None

    @staticmethod
    def _write(process: subprocess.Popen[bytes], message: dict[str, object]) -> None:
        if process.stdin is None:
            raise RuntimeError("Temporary network worker input is unavailable")
        data = (json.dumps(message, ensure_ascii=True) + "\n").encode()
        if len(data) > 65536:
            raise ValueError("Temporary network input exceeds its size limit")
        pending = memoryview(data)
        deadline = time.monotonic() + 10
        descriptor = process.stdin.fileno()
        os.set_blocking(descriptor, False)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(descriptor, selectors.EVENT_WRITE)
                while pending:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise TimeoutError("Temporary network input could not be delivered")
                    try:
                        count = process.stdin.write(pending)
                    except BlockingIOError:
                        continue
                    if count is None:
                        continue
                    if count <= 0:
                        raise BrokenPipeError("Temporary network worker input closed")
                    pending = pending[count:]
        finally:
            os.set_blocking(descriptor, True)

    def _send(self, message: dict[str, object]) -> None:
        if self.process is None:
            raise RuntimeError("Temporary network worker input is unavailable")
        self._write(self.process, message)

    def _message(self, deadline: float, *, phase: str = "readiness") -> dict[str, object]:
        process = self.process
        if process is None or process.stdout is None:
            raise RuntimeError("Temporary network worker output is unavailable")
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                if b"\n" in self.buffer:
                    line, _, rest = self.buffer.partition(b"\n")
                    self.buffer = bytearray(rest)
                    if len(line) > 8192:
                        raise RuntimeError("Temporary network acknowledgement exceeds its limit")
                    try:
                        message = json.loads(line)
                    except (UnicodeError, ValueError) as exc:
                        raise RuntimeError("Temporary network acknowledgement is invalid") from exc
                    if not isinstance(message, dict):
                        raise RuntimeError("Temporary network acknowledgement must be an object")
                    return message
                if len(self.buffer) > 8192:
                    raise RuntimeError("Temporary network acknowledgement exceeds its limit")
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError("Temporary network preparation timed out")
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk:
                    raise RuntimeError(f"Temporary network worker stopped before {phase}")
                self.buffer.extend(chunk)

    def start(self, subscription: str | None) -> NetworkReady:
        from setup_system.application.installer import Operation
        from setup_system.worker_protocol import worker_arguments

        if os.geteuid() != 0:
            raise RuntimeError("The installation coordinator must own the privilege transition")
        if self.process is not None:
            raise RuntimeError("Temporary network session is already started")
        arguments = worker_arguments(
            Operation.NETWORK_STAGE,
            self.target.name,
            self.environment,
            subscription_file=self.subscription_file,
        )
        self.process = self.factory(
            [
                "/usr/bin/python3",
                "-I",
                "-B",
                str(self.root / "src/setup_system/_worker_entry.py"),
                *arguments,
            ],
            cwd=self.target.home,
            env=self.environment,
            user=self.target.uid,
            group=self.target.gid,
            extra_groups=os.getgrouplist(self.target.name, self.target.gid),
            umask=0o077,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            bufsize=0,
        )
        try:
            self._send({"schema": 1, "operation": "start", "subscription": subscription})
            deadline = time.monotonic() + self.startup_timeout
            while True:
                message = self._message(deadline)
                if message.get("state") == "progress":
                    continue
                if message.get("state") == "failed":
                    stage = message.get("stage")
                    allowed = {"input", "temporary-client", "happ-download", "downloads", "cleanup"}
                    stage = (
                        stage
                        if isinstance(stage, str) and stage in allowed
                        else "temporary-network"
                    )
                    status = message.get("http_status")
                    detail = (
                        f" (HTTP {status})" if type(status) is int and 100 <= status <= 599 else ""
                    )
                    raise RuntimeError(f"Temporary network stage {stage} failed{detail}")
                if message.get("state") != "ready" or set(message) != {"state", "proxy", "happ"}:
                    raise RuntimeError(
                        "Temporary network setup failed; no downloads were authorized"
                    )
                proxy, happ = message["proxy"], message["happ"]
                if not isinstance(proxy, str) or not isinstance(happ, str):
                    raise RuntimeError("Temporary network acknowledgement has invalid fields")
                try:
                    parts = urlsplit(proxy)
                    port = parts.port
                except ValueError:
                    raise RuntimeError(
                        "Temporary download proxy acknowledgement is invalid"
                    ) from None
                if (
                    parts.scheme != "http"
                    or parts.hostname != "127.0.0.1"
                    or parts.username is not None
                    or parts.password is not None
                    or parts.path not in {"", "/"}
                    or parts.query
                    or parts.fragment
                    or port is None
                    or not 1024 <= port <= 65535
                ):
                    raise RuntimeError(
                        "Temporary download proxy must be a local high-port HTTP listener"
                    )
                candidate = Path(happ)
                cache = Path(self.environment["XDG_CACHE_HOME"]) / "setup-system"
                if (
                    not candidate.is_absolute()
                    or candidate.name != "happ.deb"
                    or not candidate.is_relative_to(cache)
                    or not candidate.parent.name.startswith("network-")
                    or ".." in candidate.parts
                ):
                    raise RuntimeError("Temporary Happ artifact is outside its owned session")
                self.ready = NetworkReady(proxy, candidate)
                return self.ready
        except BaseException:
            self.close(allow_failed_worker=True)
            raise

    def _terminate_group(self, process: subprocess.Popen[bytes], *, force: bool = False) -> None:
        # A dead leader can leave an engine in the same still-owned process group.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        if force:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)

    def close(self, *, allow_failed_worker: bool = False) -> None:
        process = self.process
        if process is None:
            return
        confirmed = False
        failed = False
        try:
            if process.poll() is None:
                # A failed worker can close stdin after emitting its cleanup receipt.
                # Still require the bounded CLOSED acknowledgement below.
                with contextlib.suppress(BrokenPipeError):
                    self._write(process, {"operation": "stop"})
            deadline = time.monotonic() + self.shutdown_timeout
            while not confirmed:
                message = self._message(deadline, phase="cleanup acknowledgement")
                if message == {"state": "closed"}:
                    confirmed = True
                elif message.get("state") == "failed":
                    failed = True
                else:
                    raise RuntimeError("Unexpected temporary network cleanup acknowledgement")
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
            if process.stdout is not None:
                if self.buffer.strip():
                    raise RuntimeError("Temporary network worker emitted trailing data")
                descriptor = process.stdout.fileno()
                os.set_blocking(descriptor, False)
                try:
                    trailing = os.read(descriptor, 8193)
                except BlockingIOError:
                    trailing = b""
                if trailing.strip():
                    raise RuntimeError("Temporary network worker emitted trailing data")
            if (failed or process.returncode) and not allow_failed_worker:
                raise RuntimeError("Temporary network worker failed after readiness")
            if process.returncode:
                self._terminate_group(process, force=True)
        except BaseException:
            self._terminate_group(process)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
            self._terminate_group(process, force=True)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=3)
            raise
        finally:
            self.process = None
            self.ready = None
            self.buffer.clear()
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
