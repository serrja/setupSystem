"""Bind privileged execution to installed, protected launcher records."""

import hashlib
import os
import signal
import subprocess
from pathlib import Path

import anyio
from anyio.abc import Process
from pydantic import Field, SecretBytes
from setup_system_sdk.enums.operation import WorkerMethod
from setup_system_sdk.files.manifest_types import Digest
from setup_system_sdk.files.system_paths import protected
from setup_system_sdk.operations import Receipt, Snapshot, StepPlan
from setup_system_sdk.processes import CancellationPolicy, ProcessRunner, ProcessSpec
from setup_system_sdk.schemas.common import ActionId, Identifier, Inputs
from setup_system_sdk.schemas.wire import LIMIT, Request


class TrustedExecution(Inputs):
    plugin_id: Identifier
    version: str
    release_digest: Digest
    launcher: Path
    launcher_sha256: Digest
    allowed_handlers: frozenset[ActionId]
    resources: dict[Path, Digest] = Field(max_length=4096)


class TrustStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def resolve(self, step: StepPlan) -> TrustedExecution:
        path = self.directory / (step.executor.plugin_id + ".json")
        protected(path, directory=False)
        with path.open("rb") as stream:
            raw = stream.read(512 * 1024 + 1)
        if len(raw) > 512 * 1024:
            raise ValueError("Oversized execution record")
        record = TrustedExecution.model_validate_json(raw)
        if (record.plugin_id, record.version, record.release_digest) != (
            step.executor.plugin_id,
            step.executor.version,
            step.executor.release_digest,
        ) or step.handler not in record.allowed_handlers:
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


class NativeLauncher:
    """Pass one approved step to the matching installed user-mode launcher."""

    def __init__(self, trust: TrustStore, runner: ProcessRunner) -> None:
        self.trust = trust
        self.runner = runner

    async def execute(
        self,
        record: TrustedExecution,
        snapshot: Snapshot,
        step: StepPlan,
        secret: SecretBytes,
        cancel: anyio.Event | None = None,
    ) -> Receipt:
        if self.trust.resolve(step) != record:
            raise ValueError("The trusted execution record changed")
        response = secret.get_secret_value()
        if not 0 < len(response) <= 1024:
            raise ValueError("Invalid authorization response length")
        reader, writer = os.pipe2(os.O_CLOEXEC)
        try:
            with os.fdopen(writer, "wb") as stream:
                stream.write(response)
            request = (
                Request(
                    plugin_id=step.executor.plugin_id,
                    executor=step.executor,
                    operation_id=snapshot.operation_id,
                    method=WorkerMethod.EXECUTE,
                    snapshot=snapshot,
                    step_key=step.step_key,
                )
                .model_dump_json()
                .encode()
            )
            if len(request) > LIMIT:
                raise ValueError("Native request exceeds the worker message limit")

            async def safe_boundary(child: Process) -> None:
                # The installed launcher owns the transaction and exits at its safe boundary.
                child.send_signal(signal.SIGUSR1)
                await child.wait()

            spec = ProcessSpec(
                (str(record.launcher), "--authorization-fd", str(reader)),
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                cwd=Path("/"),
                stdin=subprocess.PIPE,
                pass_fds=(reader,),
                stdout_limit=LIMIT,
                discard_stderr=True,
                cancellation=CancellationPolicy.SAFE_BOUNDARY,
                safe_boundary=safe_boundary,
            )
            result = await self.runner.run(spec, request, cancel=cancel)
            if result.returncode:
                raise ValueError("The native launcher did not return a receipt")
            receipt = Receipt.model_validate_json(result.stdout)
            receipt.validate_for(snapshot.operation_id, step)
            return receipt
        finally:
            os.close(reader)
