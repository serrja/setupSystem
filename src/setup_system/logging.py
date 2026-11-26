"""Export safe, structured events from the operational journal."""

import asyncio
import os
from pathlib import Path
from uuid import uuid4

from platformdirs import user_state_path

from setup_system.execution.user_storage import private_directory
from setup_system.storage.repositories.operations import OperationRepository


async def export_history(records: OperationRepository) -> Path:
    events = await records.events()
    directory = user_state_path("setup-system") / "v10" / "exports"
    name = f"history-{uuid4().hex}.jsonl"

    def publish() -> Path:
        with private_directory(directory) as parent:
            fd = os.open(
                name, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600, dir_fd=parent
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    for event in events:
                        stream.write(event.model_dump_json() + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.fsync(parent)
            except OSError:
                os.unlink(name, dir_fd=parent)
                raise
        return directory / name

    return await asyncio.to_thread(publish)
