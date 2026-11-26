"""Inter-process locks for operation and step identities."""

import fcntl
import hashlib
from collections.abc import AsyncIterator, Iterable
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path

import anyio
from setup_system_sdk.files.user_storage import private_directory


@asynccontextmanager
async def acquire(root: Path, keys: Iterable[str], *, wait: bool) -> AsyncIterator[None]:
    with ExitStack() as stack:
        for key in sorted(set(keys)):
            name = hashlib.sha256(key.encode()).hexdigest()
            directory = stack.enter_context(private_directory(root / name))
            while True:
                try:
                    fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if not wait:
                        raise ValueError("Another operation holds this lock") from None
                    await anyio.sleep(0.05)
        yield
