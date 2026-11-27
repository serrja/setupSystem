"""Read snapd's structured state and verify Store-issued revision assertions."""

import base64
import re
import stat
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field
from setup_system_sdk.blocking import run_sync
from setup_system_sdk.enums.operation import CancellationPolicy
from setup_system_sdk.processes import run_command

from setup_plugin_snap.catalog import Confinement, PublisherValidation
from setup_plugin_snap.store import Publisher, Revision, SocketAttributes, response_bytes

SOCKET = Path("/run/snapd.socket")


@asynccontextmanager
async def connection() -> AsyncIterator[httpx.AsyncClient]:
    meta = SOCKET.lstat()
    if SOCKET.resolve() != SOCKET or meta.st_uid != 0 or not stat.S_ISSOCK(meta.st_mode):
        raise ValueError("A protected snapd socket is required")
    async with httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(uds=str(SOCKET)),
        base_url="http://localhost/v2/",
        timeout=5,
        trust_env=False,
    ) as client:
        yield client


class Native(BaseModel):
    name: str
    id: str = ""
    revision: str
    version: str
    status: str
    publisher: Publisher | None = None
    confinement: Confinement = "strict"
    devmode: bool = False
    trymode: bool = False


class Local(BaseModel):
    type: Literal["sync"]
    result: list[Native] = Field(max_length=4096)


class Slot(BaseModel):
    snap: str
    interface: str
    attrs: SocketAttributes = Field(default_factory=SocketAttributes)


class Connections(BaseModel):
    slots: list[Slot] = Field(max_length=4096)


class Interfaces(BaseModel):
    type: Literal["sync"]
    result: Connections


class Saved(BaseModel):
    snap: str
    revision: str
    snap_id: str = Field(alias="snap-id")


class SnapshotSet(BaseModel):
    id: int = Field(gt=0)
    snapshots: list[Saved]


class Snapshots(BaseModel):
    type: Literal["sync"]
    result: list[SnapshotSet] = Field(max_length=4096)


async def snapshot_sets(client: httpx.AsyncClient, item: Revision) -> set[int]:
    reply = Snapshots.model_validate_json(
        await response_bytes(client, "snapshots", {"snaps": item.name})
    )
    return {
        group.id
        for group in reply.result
        if any(
            s.snap == item.name and s.snap_id == item.snap_id and s.revision == str(item.revision)
            for s in group.snapshots
        )
    }


async def installed(client: httpx.AsyncClient, *, all_revisions: bool = False) -> list[Native]:
    query = {"select": "all"} if all_revisions else {}
    return Local.model_validate_json(await response_bytes(client, "snaps", query)).result


async def checked_tool(*args: str, timeout: int = 20, transaction: bool = False) -> str:
    result = await run_sync(
        run_command,
        ["/usr/bin/snap", *args],
        stdin=subprocess.DEVNULL,
        cwd="/",
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "HOME": "/"},
        discard_stderr=True,
        timeout=None if transaction else timeout,
        cancellation=CancellationPolicy.SAFE_BOUNDARY
        if transaction
        else CancellationPolicy.TERMINATE,
        max_output_bytes=65536,
    )
    return result.stdout


async def content(client: httpx.AsyncClient, name: str) -> set[str]:
    reply = Interfaces.model_validate_json(
        await response_bytes(
            client, "connections", {"select": "all", "interface": "content", "snap": name}
        )
    )
    return {
        slot.attrs.content
        for slot in reply.result.slots
        if slot.snap == name and slot.interface == "content" and slot.attrs.content
    }


async def actual(
    item: Native,
    publisher_id: str | None = None,
    publisher_validation: PublisherValidation = "verified",
    confinement: Confinement = "strict",
) -> Revision:
    if item.publisher is None or item.publisher.validation != publisher_validation:
        raise ValueError("The installed publisher validation differs from the catalog")
    if publisher_id is not None and item.publisher.id != publisher_id:
        raise ValueError("The installed publisher differs from the approved identity")
    if (item.status, item.confinement, item.devmode, item.trymode) != (
        "active",
        confinement,
        False,
        False,
    ):
        raise ValueError("An installed snap has different confinement or is inactive")
    if not item.revision.isdecimal() or not re.fullmatch(r"[a-zA-Z0-9]{32}", item.id):
        raise ValueError("A signed installed revision is required")
    signed = await checked_tool(
        "known", "snap-revision", "snap-id=" + item.id, "snap-revision=" + item.revision
    )
    digests = re.findall(r"^snap-sha3-384: ([A-Za-z0-9_-]{64})$", signed, re.MULTILINE)
    if len(digests) != 1:
        raise ValueError("The local signed assertion is unavailable")
    return Revision(
        name=item.name,
        snap_id=item.id,
        revision=int(item.revision),
        version=item.version,
        sha3=base64.urlsafe_b64decode(digests[0]).hex(),
        confinement=confinement,
    )


async def preserve_data(client: httpx.AsyncClient, item: Revision) -> None:
    previous = await snapshot_sets(client, item)
    await checked_tool("save", item.name, transaction=True)
    new = (await snapshot_sets(client, item)) - previous
    if len(new) != 1:
        raise ValueError("The data snapshot could not be identified")
    await checked_tool("check-snapshot", str(new.pop()), item.name, timeout=600)
