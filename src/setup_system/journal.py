"""Append safe diagnostic events independently of the recovery ledger."""

import os
import stat
from pathlib import Path
from uuid import UUID

import anyio
from setup_system_sdk.files.user_storage import private_directory
from setup_system_sdk.operations import ActionEvent, Executor
from setup_system_sdk.schemas.common import Inputs


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

        def append() -> None:
            with private_directory(self.folder) as folder:
                descriptor = os.open(
                    "events.jsonl",
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

        await anyio.to_thread.run_sync(append)
