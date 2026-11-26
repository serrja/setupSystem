"""Read the signed Store release identity and its declared shared dependencies."""

from __future__ import annotations

import asyncio
from typing import Literal

import httpx
import yaml
from pydantic import BaseModel, Field, TypeAdapter

from setup_plugin_snap.catalog import STORE, SnapName
from setup_system_sdk.schemas.common import Inputs


class Revision(Inputs):
    name: SnapName
    snap_id: str = Field(pattern=r"^[a-zA-Z0-9]{32}$")
    revision: int = Field(gt=0)
    version: str = Field(min_length=1, max_length=128)
    sha3: str = Field(pattern=r"^[a-f0-9]{96}$")


class SocketAttributes(BaseModel):
    content: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}$")


class Plug(SocketAttributes):
    interface: str | None = None
    provider: str | None = Field(default=None, alias="default-provider")


class StoreSpec(BaseModel):
    kind: Literal["app", "base", "os", "snapd"] = Field(default="app", alias="type")
    base: str | None = None
    plugs: dict[str, Plug | None] = Field(default_factory=dict)
    slots: dict[str, Plug | None] = Field(default_factory=dict)

    def requirements(self) -> dict[str, set[str]]:
        required: dict[str, set[str]] = {}
        if self.kind == "app":
            if self.base != "none":
                required[TypeAdapter(SnapName).validate_python(self.base or "core")] = set()
            for name, plug in self.plugs.items():
                if plug and plug.provider:
                    provider = TypeAdapter(SnapName).validate_python(
                        plug.provider.partition(":")[0]
                    )
                    required.setdefault(provider, set()).add(plug.content or name)
        return required

    def content(self) -> set[str]:
        return {
            slot.content or name
            for name, slot in self.slots.items()
            if slot and slot.interface == "content"
        }


class Channel(BaseModel):
    architecture: str
    name: str
    risk: str
    track: str


class Download(BaseModel):
    sha3: str = Field(alias="sha3-384", pattern=r"^[a-f0-9]{96}$")


class Entry(BaseModel):
    channel: Channel
    revision: int
    version: str
    confinement: str
    download: Download
    manifest: str = Field(alias="snap-yaml", max_length=256000)


class Publisher(BaseModel):
    id: str
    validation: str = ""


class StoreIdentity(BaseModel):
    publisher: Publisher


class Document(BaseModel):
    name: SnapName
    snap_id: str = Field(alias="snap-id")
    snap: StoreIdentity
    channels: list[Entry] = Field(alias="channel-map", max_length=512)


async def response_bytes(
    client: httpx.AsyncClient,
    endpoint: str,
    params: dict[str, str],
    *,
    headers: dict[str, str] | None = None,
    limit: int = 8 * 1024 * 1024,
) -> bytes:
    async with (
        asyncio.timeout(15),
        client.stream("GET", endpoint, params=params, headers=headers) as response,
    ):
        response.raise_for_status()
        data = bytearray()
        async for chunk in response.aiter_bytes(65536):
            if len(data) + len(chunk) > limit:
                raise ValueError("The response exceeded the inspection limit")
            data.extend(chunk)
        return bytes(data)


async def latest(
    client: httpx.AsyncClient, name: SnapName, arch: str, identity: tuple[str, str] | None
) -> tuple[Revision, StoreSpec]:
    TypeAdapter(SnapName).validate_python(name)
    data = await response_bytes(
        client,
        STORE + name,
        {"fields": "snap-id,name,publisher,version,revision,confinement,download,snap-yaml"},
        headers={"Snap-Device-Series": "16", "Snap-Device-Architecture": arch},
    )
    document = Document.model_validate_json(data)
    publisher = document.snap.publisher
    if (
        document.name != name
        or publisher.validation != "verified"
        or (identity and (document.snap_id, publisher.id) != identity)
    ):
        raise ValueError("The Snap Store publisher or identity differs")
    matches = [
        row
        for row in document.channels
        if row.channel.architecture == arch
        and (row.channel.track, row.channel.risk, row.channel.name)
        == ("latest", "stable", "stable")
    ]
    if len(matches) != 1 or matches[0].confinement != "strict":
        raise ValueError("The Store has no unique strict latest/stable revision")
    entry = matches[0]
    return Revision(
        name=name,
        snap_id=document.snap_id,
        revision=entry.revision,
        version=entry.version,
        sha3=entry.download.sha3,
    ), StoreSpec.model_validate(yaml.safe_load(entry.manifest))
