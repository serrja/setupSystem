"""One bounded, structured stream for the application and per-plugin history."""

import asyncio
import fcntl
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import structlog
from pydantic import Field

from setup_system.infrastructure.user_storage import private_directory
from setup_system_sdk import ActionEvent, ActionId, Identifier


class Event(ActionEvent):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    plugin_id: Identifier
    operation_id: UUID
    action_id: ActionId


class Journal:
    def __init__(self, directory: Path, limit: int = 1024 * 1024) -> None:
        self.directory = directory
        self.limit = limit

    def _append(self, event: Event) -> None:
        wire = event.model_dump_json()
        with private_directory(self.directory) as directory:
            fcntl.flock(directory, fcntl.LOCK_EX)
            name = "events.jsonl"
            flags = os.O_WRONLY | os.O_NOFOLLOW | os.O_APPEND | os.O_CREAT
            fd = os.open(name, flags, 0o600, dir_fd=directory)
            with os.fdopen(fd, "ab") as output:
                if os.fstat(fd).st_size + len(wire) + 1 <= self.limit:
                    output.write(wire.encode() + b"\n")
                    return
            os.replace(name, "previous.jsonl", src_dir_fd=directory, dst_dir_fd=directory)
            with os.fdopen(
                os.open(name, flags | os.O_EXCL, 0o600, dir_fd=directory), "ab"
            ) as output:
                output.write(wire.encode() + b"\n")

    async def emit(self, event: Event) -> None:
        await asyncio.to_thread(self._append, event)
        structlog.wrap_logger(
            logging.getLogger("setup_system.events"),
            processors=[
                structlog.processors.EventRenamer("message_code"),
                structlog.processors.JSONRenderer(),
            ],
        ).info(event.message_code, **event.model_dump(mode="json", exclude={"message_code"}))

    def _history(self, plugin: str | None) -> list[Event]:
        events: list[Event] = []
        try:
            with private_directory(self.directory, create=False) as directory:
                fcntl.flock(directory, fcntl.LOCK_SH)
                for name in ("previous.jsonl", "events.jsonl"):
                    try:
                        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
                    except FileNotFoundError:
                        continue
                    with os.fdopen(fd, "rb") as stream:
                        for line in stream.read(self.limit).splitlines():
                            item = Event.model_validate_json(line)
                            if plugin is None or item.plugin_id == plugin:
                                events.append(item)
        except FileNotFoundError:
            return []
        return events[-250:]

    async def history(self, plugin: str | None = None) -> list[Event]:
        return await asyncio.to_thread(self._history, plugin)

    async def export(self) -> Path:
        """Export the same bounded, allowlisted events as the global history."""
        events = await self.history()

        def write() -> Path:
            folder = self.directory.parent / "reports"
            name = "history-" + uuid4().hex + ".jsonl"
            with private_directory(folder) as fd:
                descriptor = os.open(
                    name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=fd
                )
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    for event in events:
                        stream.write(event.model_dump_json() + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.fsync(fd)
            return folder / name

        return await asyncio.to_thread(write)
