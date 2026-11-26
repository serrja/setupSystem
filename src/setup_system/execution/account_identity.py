"""Verified POSIX account identity shared with early source delivery."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TargetUser:
    name: str
    uid: int
    gid: int
    home: Path
    shell: str
