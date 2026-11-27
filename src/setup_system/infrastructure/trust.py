"""Bind privileged execution to installed, protected launcher records."""

import hashlib
import os
import signal
import subprocess
from pathlib import Path

import anyio
from anyio.abc import Process
from pydantic import SecretBytes
from setup_system_sdk.enums.operation import WorkerMethod
from setup_system_sdk.files.system_paths import protected
from setup_system_sdk.native_execution import NativeRequest, ProviderBinding
from setup_system_sdk.native_execution import TrustedExecution as TrustedExecution
from setup_system_sdk.operations import Executor, Receipt, Snapshot, StepPlan
from setup_system_sdk.processes import CancellationPolicy, ProcessRunner, ProcessSpec
from setup_system_sdk.schemas.wire import LIMIT, Request


class TrustStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def resolve(
        self,
        step: StepPlan,
        providers: dict[str, ProviderBinding] | None = None,
        planned: tuple[StepPlan, ...] = (),
    ) -> TrustedExecution:
        record = self.resolve_handler(step.executor, step.handler)
        for delegated in record.validate_providers(step, providers or {}, planned):
            provider = self.resolve_handler(delegated.executor, delegated.handler)
            if (provider.launcher, provider.launcher_sha256) != (
                record.launcher,
                record.launcher_sha256,
            ):
                raise ValueError("Legacy provider steps require the same installed native launcher")
        return record

    def resolve_handler(self, executor: Executor, handler: str) -> TrustedExecution:
        path = self.directory / (executor.plugin_id + ".json")
        protected(path, directory=False)
        with path.open("rb") as stream:
            raw = stream.read(512 * 1024 + 1)
        if len(raw) > 512 * 1024:
            raise ValueError("Oversized execution record")
        record = TrustedExecution.model_validate_json(raw)
        if (record.plugin_id, record.version, record.release_digest) != (
            executor.plugin_id,
            executor.version,
            executor.release_digest,
        ) or handler not in record.allowed_handlers:
            raise ValueError("Step is not authorized by this execution record")
        for item, digest in [(record.launcher, record.launcher_sha256), *record.resources.items()]:
            if not item.is_absolute() or item.resolve() != item:
                raise ValueError("Invalid protected execution path")
            protected(item, directory=False)
            with item.open("rb") as content:
                if hashlib.file_digest(content, "sha256").hexdigest() != digest:
                    raise ValueError("Protected execution resource changed")
        if not os.access(record.launcher, os.X_OK):
            raise ValueError("Protected launcher is unavailable")
        return record


async def request_safe_boundary(child: Process) -> None:
    """Deliver the cancellation signal once the installed launcher can handle it."""
    status = Path("/proc") / str(child.pid) / "status"
    mask = 1 << (signal.SIGUSR1 - 1)
    while child.returncode is None:
        try:
            caught = next(
                line for line in status.read_text().splitlines() if line.startswith("SigCgt:")
            )
        except (OSError, StopIteration):
            # Let the executor finish if its signal disposition cannot be observed.
            break
        if int(caught.split()[1], 16) & mask:
            child.send_signal(signal.SIGUSR1)
            break
        await anyio.sleep(0.025)
    await child.wait()


class NativeLauncher:
    """Pass one approved step to the matching installed user-mode launcher."""

    def __init__(
        self, trust: TrustStore, runner: ProcessRunner, *, prompt_timeout: float = 30
    ) -> None:
        self.trust = trust
        self.runner = runner
        self.prompt_timeout = prompt_timeout

    async def execute(
        self,
        record: TrustedExecution,
        snapshot: Snapshot,
        step: StepPlan,
        secret: SecretBytes,
        cancel: anyio.Event | None = None,
        providers: dict[str, ProviderBinding] | None = None,
    ) -> Receipt:
        if cancel is not None and cancel.is_set():
            raise ValueError("Authorization stopped before native execution")
        if self.trust.resolve(step, providers, snapshot.plan.steps) != record:
            raise ValueError("The trusted execution record changed")
        response = secret.get_secret_value()
        if not 0 < len(response) <= 1024:
            raise ValueError("Invalid authorization response length")
        reader, writer = os.pipe2(os.O_CLOEXEC)
        try:
            with os.fdopen(writer, "wb") as stream:
                stream.write(response)
            base = Request(
                plugin_id=step.executor.plugin_id,
                executor=step.executor,
                operation_id=snapshot.operation_id,
                method=WorkerMethod.EXECUTE,
                snapshot=snapshot,
                step_key=step.step_key,
            )
            if record.fact_providers is None or step.capability:
                request = base.model_dump_json().encode()
            else:
                request = (
                    NativeRequest(**base.model_dump(), providers=providers or {})
                    .model_dump_json()
                    .encode()
                )
            if len(request) > LIMIT:
                raise ValueError("Native request exceeds the worker message limit")

            spec = ProcessSpec(
                (str(record.launcher), "--authorization-fd", str(reader)),
                env={
                    "PATH": "/usr/bin:/bin",
                    "LANG": "C.UTF-8",
                    "SETUP_SYSTEM_NATIVE_PROMPT_TIMEOUT": str(self.prompt_timeout),
                },
                cwd=Path("/"),
                stdin=subprocess.PIPE,
                pass_fds=(reader,),
                timeout=None,
                stdout_limit=LIMIT,
                discard_stderr=True,
                cancellation=CancellationPolicy.SAFE_BOUNDARY,
                safe_boundary=request_safe_boundary,
            )
            result = await self.runner.run(spec, request, cancel=cancel)
            if result.returncode:
                raise ValueError("The native launcher did not return a receipt")
            receipt = Receipt.model_validate_json(result.stdout)
            receipt.validate_for(snapshot.operation_id, step)
            return receipt
        finally:
            os.close(reader)
