"""Bound private native request and result reads before parsing their schemas."""

import hashlib
import os
import stat
from pathlib import Path

from setup_system.domain.administration import ADMIN_REQUEST_BYTES
from setup_system.domain.contracts import MAX_MESSAGE_BYTES
from setup_system.models import RequestEnvelope


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


def read_admin_request(path: str, action: str, identity: str | None = None) -> dict[str, str]:
    """Freeze the bounded play input before su and bind it to the pre-prompt selection."""
    if action not in {"inspect", "plan", "apply"}:
        raise ValueError("Unknown administrative operation")
    raw = read_wire(Path(path), limit=ADMIN_REQUEST_BYTES, private=True)
    digest = hashlib.sha256(raw).hexdigest()
    if identity is not None and identity != digest:
        raise ValueError("The administrative request changed after confirmation")
    envelope = RequestEnvelope.model_validate_json(raw).for_target(f"admin-{action}")
    # The consent digest above binds the original bytes. Only the validated transport
    # copy is compacted for the helper, where it is parsed as UTF-8 JSON again.
    wire = envelope.model_dump_json(by_alias=True, exclude_unset=True)
    if len(wire.encode()) > ADMIN_REQUEST_BYTES:
        raise ValueError("The encoded administrative request exceeds its size limit")
    return {"wire": wire, "digest": digest}
