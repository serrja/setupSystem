"""Hold running generations and retire only unmodified extension code."""

import fcntl
import hashlib
import os
import re
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from setup_system.extensions import (
    Activation,
    _directory,
    _local_file,
    _Pointer,
    _private,
    _read_generation,
)
from setup_system.infrastructure import retirement_checkpoint as checkpoint
from setup_system.infrastructure.recorded_tree import record_tree, remove_recorded
from setup_system_sdk import Inputs, Manifest

_GENERATION = re.compile(r"^[0-9a-f]{32}$")
_SEAL = "generation.sha256"


def fingerprint(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        if path == folder / _SEAL:
            continue
        info = path.lstat()
        digest.update(
            os.fsencode(path.relative_to(folder)) + b"\0" + str(info.st_mode).encode() + b"\0"
        )
        if stat.S_ISLNK(info.st_mode):
            digest.update(os.fsencode(os.readlink(path)))
        elif stat.S_ISREG(info.st_mode):
            with path.open("rb") as stream:
                digest.update(hashlib.file_digest(stream, "sha256").digest())
        elif not stat.S_ISDIR(info.st_mode):
            raise ValueError("Extension contains an unexpected special file")
    return digest.hexdigest()


def seal_generation(folder: Path) -> None:
    _private(folder)
    digest = fingerprint(folder)
    with (folder / _SEAL).open("x") as stream:
        stream.write(digest)
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def generation_lock(folder: Path, *, exclusive: bool) -> Iterator[None]:
    fd = os.open(folder, os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("The extension is in use") from None
        yield
    finally:
        os.close(fd)


def generation(state: Activation) -> Path:
    return state.python.parents[2]


def toggle(env_root: Path, plugin: str, *, enable: bool) -> None:
    root = _directory(env_root, plugin)
    _private(root)
    with generation_lock(root, exclusive=True):
        source, dest = (
            ("disabled.json", "active.json") if enable else ("active.json", "disabled.json")
        )
        pointer = _Pointer.model_validate_json(_local_file(root / source, 4096))
        if pointer.plugin_id != plugin or (root / dest).exists():
            raise ValueError("Extension state is ambiguous")
        state = _read_generation(root, pointer)
        with generation_lock(generation(state), exclusive=True):
            os.rename(root / source, root / dest)


_RETIRING = ".retiring.json"


class Retirement(Inputs):
    manifest: Manifest
    entries: tuple[str, ...]
    files: dict[str, tuple[int, int, int, str]]


def _retirement(root: Path) -> Retirement:
    data = (
        checkpoint.load(root, _RETIRING).payload.encode()
        if checkpoint.pending(root)
        else _local_file(root / _RETIRING, 64 * 1024 * 1024)
    )
    record = Retirement.model_validate_json(data)
    if record.manifest.id != root.name:
        raise ValueError("The retirement record belongs to another extension")
    return record


def retirement_manifest(root: Path) -> Manifest:
    return _retirement(root).manifest


def removable(env_root: Path, plugin: str) -> tuple[str, ...]:
    root = _directory(env_root, plugin)
    if checkpoint.pending(root):
        checkpoint.check_target(root, checkpoint.load(root, _RETIRING))
        return _retirement(root).entries
    _private(root)
    if os.path.lexists(root / _RETIRING):
        record = _retirement(root)
        if any(record.files.get(k) != v for k, v in record_tree(root, root / _RETIRING).items()):
            raise ValueError("Extension changed during interrupted removal")
        return record.entries
    files = []
    pointers = [p for p in ("active.json", "disabled.json") if (root / p).is_file()]
    if len(pointers) != 1:
        raise ValueError("Review the incomplete extension state before removing it")
    for entry in sorted(root.iterdir()):
        if entry.name in pointers:
            _Pointer.model_validate_json(_local_file(entry, 4096))
        else:
            if not _GENERATION.fullmatch(entry.name):
                raise ValueError("Extension storage contains an unrecognized entry")
            _private(entry)
            if _local_file(entry / _SEAL, 128).decode() != fingerprint(entry):
                raise ValueError("Extension files changed after installation")
        files.append(entry.name)
    return tuple(files)


def retire(env_root: Path, plugin: str, expected: tuple[str, ...]) -> None:
    root = _directory(env_root, plugin)
    if checkpoint.pending(root):
        record = _retirement(root)
        if record.entries != expected:
            raise ValueError("Retirement differs from confirmation")
        checkpoint.finish(root, _RETIRING, record.model_dump_json().encode())
        return
    with generation_lock(root, exclusive=True), ExitStack() as locks:
        if removable(env_root, plugin) != expected:
            raise ValueError("Extension changed after confirmation")
        for name in expected:
            if _GENERATION.fullmatch(name) and (root / name).exists():
                locks.enter_context(generation_lock(root / name, exclusive=True))
        if not os.path.lexists(root / _RETIRING):
            pointer_name = next(n for n in expected if n in ("active.json", "disabled.json"))
            pointer = _Pointer.model_validate_json(_local_file(root / pointer_name, 4096))
            state = _read_generation(root, pointer)
            record = Retirement(
                manifest=state.manifest, entries=expected, files=record_tree(root, root / _RETIRING)
            )
            if removable(env_root, plugin) != expected:
                raise ValueError("Extension changed while preparing its retirement")
            checkpoint.publish(root, _RETIRING, record.model_dump_json().encode(), env_root)
        else:
            record = _retirement(root)
        remove_recorded(root, record.files, root / _RETIRING)
        checkpoint.finish(root, _RETIRING, record.model_dump_json().encode())
