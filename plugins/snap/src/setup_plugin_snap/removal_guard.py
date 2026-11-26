"""Check the native snapd inventory immediately before a reviewed snapd removal."""

import os
import stat
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field, JsonValue

from setup_system.layout import require_installed_resource

SOCKET = Path("/run/snapd.socket")


class Inventory(BaseModel):
    type: Literal["sync"]
    result: list[JsonValue] = Field(max_length=8192)


def require_empty() -> None:
    info = SOCKET.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0 or SOCKET.resolve() != SOCKET:
        raise ValueError("A root-owned local snapd socket is required.")
    with httpx.Client(
        transport=httpx.HTTPTransport(uds=str(SOCKET)), timeout=5, trust_env=False
    ) as client:
        for path in ("snaps?select=all", "snapshots", "changes?select=in-progress"):
            with client.stream("GET", "http://localhost/v2/" + path) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes(65536):
                    if len(body) + len(chunk) > 1024 * 1024:
                        raise ValueError("The snapd inventory exceeds the removal limit.")
                    body.extend(chunk)
            if Inventory.model_validate_json(body).result:
                raise ValueError("Retain snapd while snaps, snapshots or active changes exist.")


if __name__ == "__main__":
    require_installed_resource(Path(__file__), "plugins/snap/setup_plugin_snap/removal_guard.py")
    if os.geteuid() != 0:
        raise SystemExit(2)
    require_empty()
