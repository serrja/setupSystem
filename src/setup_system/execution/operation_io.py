"""Bound private native request and result reads before parsing their schemas."""

import os
import stat
from pathlib import Path

from setup_system.execution.contracts import MAX_MESSAGE_BYTES


def read_wire(path: Path, *, limit: int = MAX_MESSAGE_BYTES, private: bool = False) -> bytes:
    if not 0 < limit <= MAX_MESSAGE_BYTES:
        raise ValueError("Invalid native operation limit")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o022
            or (private and stat.S_IMODE(info.st_mode) != 0o600)
            or info.st_size > limit
        ):
            raise ValueError("Invalid native operation file")
        with open(descriptor, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
    finally:
        os.close(descriptor)
    if len(data) > limit:
        raise ValueError("Native operation exceeds the supported size")
    return data
