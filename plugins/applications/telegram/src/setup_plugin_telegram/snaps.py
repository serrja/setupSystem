"""Resolve signed strict Store revisions and verify snapd's installed assertions."""

from __future__ import annotations

import base64
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from time import monotonic
from typing import Annotated, Literal

import httpx
import yaml
from pydantic import BaseModel, Field, TypeAdapter

from setup_system.infrastructure.native_target import current_target
from setup_system.infrastructure.processes import run_command
from setup_system.layout import require_installed_resource
from setup_system_sdk import (
    Inputs,
    LifecycleAction,
    Outcome,
    Plan,
    Resource,
    Result,
    owned_resources,
)
from setup_system_sdk.apt import installed

NAME = "telegram-desktop"
SNAP_ID = "jpdoHqMd90M39DdH2JSZbeQxipqHtwLo"
PUBLISHER = "EXRxowJU0BSPUYQhLXu58z3mwKY33Hcp"
SYSTEM_SNAP = "snapd"
SYSTEM_SNAP_ID = "PMrrV4ml8uWuEUDBT8dSGnKUYbevVhc4"
IDENTITIES = {NAME: (SNAP_ID, PUBLISHER), SYSTEM_SNAP: (SYSTEM_SNAP_ID, "canonical")}
SNAP = "/usr/bin/snap"
SOCKET = Path("/run/snapd.socket")
ORIGIN = "https://api.snapcraft.io/v2/snaps/info/"
SnapName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")]
Action = Literal[LifecycleAction.INSTALL, LifecycleAction.UPDATE, LifecycleAction.UNINSTALL]
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "HOME": "/"}


def package_action(action: str) -> Action:
    return TypeAdapter(Action).validate_python(action)


class Revision(Inputs):
    name: SnapName
    snap_id: str = Field(pattern=r"^[a-zA-Z0-9]{32}$")
    revision: int = Field(gt=0)
    version: str = Field(min_length=1, max_length=128)
    sha3: str = Field(pattern=r"^[a-f0-9]{96}$")


class Native(BaseModel):
    name: str
    id: str = ""
    revision: str
    version: str
    status: str
    confinement: str = ""
    devmode: bool = False
    trymode: bool = False


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


class MissingContentError(ValueError):
    """A required slot is absent from a shared provider."""


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


def _payload(response: httpx.Response, limit: int) -> bytes:
    response.raise_for_status()
    data = bytearray()
    deadline = monotonic() + 12
    for chunk in response.iter_bytes(65536):
        if len(data) + len(chunk) > limit or monotonic() > deadline:
            raise ValueError("Snap metadata exceeded its inspection limit")
        data.extend(chunk)
    return bytes(data)


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


def latest(name: str, arch: str) -> tuple[Revision, StoreSpec]:
    TypeAdapter(SnapName).validate_python(name)
    with (
        httpx.Client(follow_redirects=False, timeout=8, trust_env=False) as client,
        client.stream(
            "GET",
            ORIGIN + name,
            headers={"Snap-Device-Series": "16", "Snap-Device-Architecture": arch},
            params={
                "fields": "snap-id,name,publisher,version,revision,confinement,download,snap-yaml"
            },
        ) as response,
    ):
        document = Document.model_validate_json(_payload(response, 8 * 1024 * 1024))
    publisher = document.snap.publisher
    if (
        document.name != name
        or publisher.validation != "verified"
        or (name in IDENTITIES and (document.snap_id, publisher.id) != IDENTITIES[name])
    ):
        raise ValueError("The Snap Store publisher or identity differs")
    entries = [
        entry
        for entry in document.channels
        if (entry.channel.architecture, entry.channel.name, entry.channel.risk, entry.channel.track)
        == (arch, "stable", "stable", "latest")
    ]
    if len(entries) != 1 or entries[0].confinement != "strict":
        raise ValueError("No unique strict latest/stable release on this architecture")
    row = entries[0]
    revision = Revision(
        name=name,
        snap_id=document.snap_id,
        revision=row.revision,
        version=row.version,
        sha3=row.download.sha3,
    )
    spec = StoreSpec.model_validate(yaml.safe_load(row.manifest))
    return revision, spec


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


def daemon_data(endpoint: str, **params: str) -> bytes:
    info = SOCKET.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0 or SOCKET.resolve() != SOCKET:
        raise ValueError("Snap requires the root-owned native snapd socket")
    with (
        httpx.Client(
            transport=httpx.HTTPTransport(uds=str(SOCKET)), timeout=5, trust_env=False
        ) as client,
        client.stream("GET", "http://localhost/v2/" + endpoint, params=params) as response,
    ):
        return _payload(response, 16 * 1024 * 1024)


def local_content(name: str) -> set[str]:
    slots = Interfaces.model_validate_json(
        daemon_data("connections", select="all", interface="content", snap=name)
    ).result.slots
    return {
        slot.attrs.content
        for slot in slots
        if slot.snap == name and slot.interface == "content" and slot.attrs.content
    }


def local() -> dict[str, Native]:
    rows = Local.model_validate_json(daemon_data("snaps")).result
    if len({row.name for row in rows}) != len(rows):
        raise ValueError("Ambiguous active snap inventory")
    return {row.name: row for row in rows}


def cli(*args: str, timeout: int = 20) -> str:
    return run_command(
        [SNAP, *args],
        stdin=subprocess.DEVNULL,
        env=ENV,
        cwd="/",
        discard_stderr=True,
        max_output_bytes=65536,
        timeout=timeout,
    ).stdout


def actual(item: Native) -> Revision:
    if (item.status, item.confinement, item.devmode, item.trymode) != (
        "active",
        "strict",
        False,
        False,
    ):
        raise ValueError("An existing snap is inactive or has different confinement")
    if not item.revision.isdecimal() or not re.fullmatch(r"[a-zA-Z0-9]{32}", item.id):
        raise ValueError("The installed snap has no signed Store identity")
    output = cli("known", "snap-revision", "snap-id=" + item.id, "snap-revision=" + item.revision)
    digests = re.findall(r"^snap-sha3-384: ([A-Za-z0-9_-]{64})$", output, re.MULTILINE)
    if len(digests) != 1:
        raise ValueError("Cannot verify the installed revision assertion")
    return Revision(
        name=item.name,
        snap_id=item.id,
        revision=int(item.revision),
        version=item.version,
        sha3=base64.urlsafe_b64decode(digests[0]).hex(),
    )


def release_plan(
    action: Action,
    *,
    confirmed: Resource | None = None,
    inspected: dict[str, StoreSpec] | None = None,
) -> Plan:
    specs = {} if inspected is None else inspected
    own = next(
        (
            r
            for r in owned_resources()
            if r.kind == "snap" and r.identity == NAME and r.ownership == "created" and r.cleanup
        ),
        None,
    )
    if action == LifecycleAction.UNINSTALL and not (own or confirmed):
        return Plan(outcome=Outcome.NO_CHANGES, summary="No owned Telegram snap to remove")
    arch, supported = current_target()
    if not supported:
        return Plan(
            outcome=Outcome.BLOCKED, summary="Telegram Snap requires a supported Debian target"
        )
    if action != LifecycleAction.UNINSTALL and installed(NAME):
        return Plan(
            outcome=Outcome.BLOCKED,
            summary="Review the existing Debian Telegram installation before switching to Snap.",
        )
    snaps = local()
    current = actual(snaps[NAME]) if NAME in snaps else None
    if current and current.snap_id != SNAP_ID:
        return Plan(
            outcome=Outcome.BLOCKED,
            summary="A different existing Telegram installation was preserved",
        )
    expected = confirmed.before if confirmed else own.after if own else None
    before = current.model_dump_json() if current else None
    if action == LifecycleAction.UNINSTALL:
        if not current:
            return Plan(outcome=Outcome.NO_CHANGES, summary="The Telegram snap is absent")
        if expected != before:
            return Plan(
                outcome=Outcome.BLOCKED,
                summary="Preserve this preexisting or changed Telegram snap",
            )
        return Plan(
            summary="Verify a manual data snapshot, then remove only Telegram Desktop",
            warnings=[
                "Close Telegram. Snap saves data for all users. Find the set ID with snap saved."
            ],
            resources=[
                Resource(
                    kind="snap",
                    identity=NAME,
                    before=before,
                    ownership="created",
                    effect="remove",
                    cleanup=True,
                )
            ],
        )
    if current and action == LifecycleAction.INSTALL:
        return Plan(
            outcome=Outcome.NO_CHANGES,
            summary="Telegram Desktop is already installed",
            resources=[
                Resource(
                    kind="snap", identity=NAME, before=before, after=before, ownership="preexisting"
                )
            ],
        )
    if current and expected != before:
        return Plan(
            outcome=Outcome.BLOCKED,
            summary="Review the unmanaged or changed Telegram installation in Snap.",
        )
    if not current and action == LifecycleAction.UPDATE:
        return Plan(outcome=Outcome.BLOCKED, summary="Install Telegram Desktop first")
    resources: list[Resource] = []
    visited: set[str] = set()
    chain: set[str] = set()

    def select(name: str) -> None:
        if name in chain or len(visited) + len(chain) >= 24:
            raise ValueError("Ambiguous or oversized snap prerequisites")
        if name in visited:
            return
        chain.add(name)
        existing = actual(snaps[name]) if name in snaps else None
        if name != NAME and existing:
            if name in IDENTITIES and existing.snap_id != IDENTITIES[name][0]:
                raise ValueError("An existing system snap has a different issued identity")
            target = existing
        else:
            target, spec = latest(name, arch)
            specs[name] = spec
            if existing and target.revision < existing.revision:
                raise ValueError("The reviewed channel would downgrade the installation")
            for dep, tags in sorted(spec.requirements().items()):
                select(dep)
                if tags:
                    provided = local_content(dep) if dep in snaps else specs[dep].content()
                    if not tags <= provided:
                        raise MissingContentError(dep)
        old = existing.model_dump_json() if existing else None
        resources.append(
            Resource(
                kind="snap",
                identity=name,
                before=old,
                after=target.model_dump_json(),
                ownership=("created" if not existing else "modified") if name == NAME else "shared",
                cleanup=name == NAME and existing is None,
            )
        )
        chain.remove(name)
        visited.add(name)

    # On seeded Debian, snapd adds its Store snap before installing applications.
    select(SYSTEM_SNAP)
    select(NAME)
    return Plan(
        summary="Review the Telegram Desktop strict latest/stable revision and shared runtimes",
        resources=resources,
        outcome=Outcome.READY
        if any(row.before != row.after for row in resources)
        else Outcome.NO_CHANGES,
        warnings=[
            "The system snap may restart snapd. Shared runtimes stay available to other snaps."
        ],
    )


def prepare(action: Action) -> Plan:
    try:
        return release_plan(action)
    except MissingContentError as exc:
        return Plan(
            outcome=Outcome.BLOCKED,
            summary=f"Review the required content slots of {exc} before installing Telegram.",
        )
    except (ValueError, OSError, httpx.HTTPError, subprocess.SubprocessError, yaml.YAMLError):
        return Plan(
            outcome=Outcome.BLOCKED,
            summary="Check snapd, the Snap Store and the local revision assertions.",
        )


def verify(plan: Plan) -> Result:
    try:
        items = local()
        rows = []
        for row in plan.resources:
            item = items.get(row.identity)
            ok = (
                item is None
                if row.effect == "remove"
                else bool(item and actual(item).model_dump_json() == row.after)
            )
            if ok:
                rows.append(row.model_copy(update={"verified": True}))
    except (ValueError, OSError, httpx.HTTPError, subprocess.SubprocessError):
        return Result(
            outcome=Outcome.PARTIAL, message="Check the local daemon and installed revisions"
        )
    return Result(
        outcome=Outcome.COMPLETE if len(rows) == len(plan.resources) else Outcome.PARTIAL,
        message="Checked installed snap revisions and signed assertions",
        resources=rows,
    )


class Approval(Inputs):
    action: Action
    plan: Plan


def apply(approval: Approval) -> None:
    if os.geteuid() != 0:
        raise ValueError("The installed administrative helper is required")
    rel = "plugins/telegram/setup_plugin_telegram/snaps.py"
    require_installed_resource(Path(__file__), rel)
    action, plan = approval.action, approval.plan
    if plan.outcome != Outcome.READY or not plan.resources:
        raise ValueError("Expected a reviewed native change")
    approved = next((r for r in plan.resources if r.identity == NAME and r.kind == "snap"), None)
    inspected: dict[str, StoreSpec] = {}
    checked = release_plan(action, confirmed=approved, inspected=inspected)
    if checked != plan:
        raise ValueError("Snap revision or local state changed after confirmation")
    if action == LifecycleAction.UNINSTALL:
        saved = cli("save", NAME, timeout=600)
        ids = re.findall(r"^[ \t]*([0-9]+)\s+telegram-desktop\s", saved, re.MULTILINE)
        if len(ids) != 1:
            raise ValueError("A verified data snapshot is required before removing Telegram")
        cli("check-snapshot", ids[0], NAME, timeout=600)
        if release_plan(action, confirmed=approved) != plan:
            raise ValueError("Telegram changed during its data snapshot")
        cli("remove", NAME, timeout=600)
    else:
        for row in plan.resources:
            if row.before == row.after or not row.after:
                continue
            version = Revision.model_validate_json(row.after)
            present = local()
            requirements = inspected[version.name].requirements()
            if version.name != SYSTEM_SNAP:
                requirements.setdefault(SYSTEM_SNAP, set())
            for name, tags in requirements.items():
                dependency = next(r for r in checked.resources if r.identity == name)
                if (
                    name not in present
                    or actual(present[name]).model_dump_json() != dependency.after
                ):
                    raise ValueError("A reviewed shared snap dependency changed")
                if tags and not tags <= local_content(name):
                    raise MissingContentError(name)
            item = present.get(version.name)
            before = actual(item).model_dump_json() if item else None
            if before != row.before:
                raise ValueError("A snap changed before the reviewed transaction")
            change = cli(
                "refresh" if before else "install",
                "--no-wait",
                "--channel=latest/stable",
                "--revision=" + str(version.revision),
                version.name,
            ).strip()
            if not re.fullmatch(r"[1-9][0-9]*", change):
                raise ValueError("Snapd did not start the requested change")
            cli("watch", change, timeout=600)
            published = local().get(version.name)
            if not published or actual(published) != version:
                raise ValueError("Snapd applied a different revision")
    if verify(plan).outcome != Outcome.COMPLETE:
        raise ValueError("Snapd has not confirmed every reviewed revision")


if __name__ == "__main__":
    data = sys.stdin.buffer.read(262145)
    if len(data) > 262144:
        raise SystemExit(2)
    apply(Approval.model_validate_json(data))
