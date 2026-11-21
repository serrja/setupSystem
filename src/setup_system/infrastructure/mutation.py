"""Serialize independent Textual processes targeting the same normal user's HOME."""

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def user_operation(home: Path | None = None) -> Iterator[None]:
    home = Path.home() if home is None else home
    descriptor = os.open(home, os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_RDONLY)
    try:
        if os.fstat(descriptor).st_uid != os.getuid():
            raise ValueError("A mutating operation requires the target user's HOME")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another configuration operation is running for this user") from None
        from setup_system.infrastructure.encryption import validate_home_access

        validate_home_access(home)
        yield
    finally:
        os.close(descriptor)
