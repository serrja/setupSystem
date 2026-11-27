"""Append safe diagnostic events independently of the recovery ledger."""

import json
import os
import re
import stat
import traceback
from pathlib import Path
from uuid import UUID

import anyio
from setup_system_sdk.files.user_storage import private_directory
from setup_system_sdk.operations import ActionEvent, Executor
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.errors import Error


class JournalEvent(Inputs):
    operation_id: UUID
    executor: Executor
    event: ActionEvent


class Journal:
    def __init__(self, folder: Path) -> None:
        self.folder = folder

    async def emit(self, operation: UUID, executor: Executor, event: ActionEvent) -> None:
        line = (
            JournalEvent(operation_id=operation, executor=executor, event=event)
            .model_dump_json()
            .encode()
            + b"\n"
        )

        await anyio.to_thread.run_sync(self._append, "events.jsonl", line)

    def _append(self, name: str, line: bytes) -> None:
        with private_directory(self.folder) as folder:
            descriptor = os.open(
                name,
                os.O_APPEND
                | os.O_WRONLY
                | os.O_CREAT
                | os.O_CLOEXEC
                | os.O_NOFOLLOW
                | os.O_NONBLOCK,
                0o600,
                dir_fd=folder,
            )
            try:
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_nlink != 1
                    or info.st_mode & 0o077
                ):
                    raise ValueError("Unsafe diagnostic journal")
                os.write(descriptor, line)
            finally:
                os.close(descriptor)

    def record_failure(
        self,
        detail: Error,
        failure: BaseException,
        *,
        stage: str,
        plugin_id: str | None = None,
        executor: Executor | None = None,
        exit_code: int | None = None,
    ) -> None:
        """Write bounded metadata and stack locations without exception text or locals."""

        def leaves(error: BaseException) -> list[BaseException]:
            if isinstance(error, BaseExceptionGroup):
                return [leaf for child in error.exceptions for leaf in leaves(child)]
            return [error]

        frames = traceback.extract_tb(failure.__traceback__, limit=24)
        safe_frames = [
            {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
            for frame in frames
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", frame.name)
            and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", Path(frame.filename).name)
        ]
        safe_context = {}
        for key, value in detail.context.items():
            if key in {"stage", "component", "module"} and re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_.-]{0,127}", value
            ):
                safe_context[key] = value
            elif key == "hostname" and re.fullmatch(r"[a-z0-9.-]{1,253}", value):
                safe_context[key] = value
            elif key in {"http_status", "exit_code"} and re.fullmatch(r"[0-9]{1,3}", value):
                safe_context[key] = value
        exception_names = [type(leaf).__name__ for leaf in leaves(failure)[:16]]
        record = {
            "diagnostic_id": str(detail.diagnostic_id) if detail.diagnostic_id else None,
            "operation_id": str(detail.operation_id) if detail.operation_id else None,
            "code": detail.code.value,
            "stage": stage,
            "plugin_id": (
                plugin_id
                if plugin_id and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", plugin_id)
                else None
            ),
            "executor": executor.model_dump(mode="json") if executor else None,
            "exit_code": exit_code,
            "exception_types": [
                name
                for name in exception_names
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name)
            ],
            "context": safe_context,
            "frames": safe_frames,
        }
        line = (json.dumps(record, separators=(",", ":")) + "\n").encode()
        self._append("failures.jsonl", line)

    async def emit_failure(
        self,
        detail: Error,
        failure: BaseException,
        *,
        stage: str,
        plugin_id: str | None = None,
        executor: Executor | None = None,
        exit_code: int | None = None,
    ) -> None:
        await anyio.to_thread.run_sync(
            lambda: self.record_failure(
                detail,
                failure,
                stage=stage,
                plugin_id=plugin_id,
                executor=executor,
                exit_code=exit_code,
            )
        )
