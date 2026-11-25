"""Identify and manage individual GitHub authentication keys through its public API."""

import base64
import hashlib
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import httpx
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_ssh_public_key
from pydantic import BaseModel, Field, TypeAdapter, field_validator

from setup_system.infrastructure.processes import run_command
from setup_system.infrastructure.user_storage import capture_receipt, inside_home
from setup_system_sdk import Inputs, Outcome, Plan, Resource, Result, owned_resources
from setup_system_sdk.native import command

ACCOUNT_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$"
AccountName = Annotated[str, Field(min_length=1, max_length=100, pattern=ACCOUNT_PATTERN)]


class KeyAccount(Inputs):
    host: str = Field(default="github.com", min_length=1, max_length=253)
    account: AccountName


class PublicKey(KeyAccount):
    public_key: Path = Field(
        title="OpenSSH public key", json_schema_extra={"ui": {"widget": "path"}}
    )


class Upload(PublicKey):
    title: str = Field(min_length=1, max_length=80)

    @field_validator("title")
    @classmethod
    def plain_title(cls, value: str) -> str:
        if not value.strip() or not value.isprintable():
            raise ValueError("Enter a printable key title")
        return value


class Probe(PublicKey):
    login: str = Field(
        default="git", title="SSH transport user", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$"
    )


class Revoke(KeyAccount):
    key_id: int = Field(gt=0, title="Remote key ID")


class RemoteKey(BaseModel):
    id: int = Field(gt=0)
    key: str = Field(max_length=16384)


def public_material(key: bytes) -> tuple[str, str]:
    valid = load_ssh_public_key(key)
    canonical = valid.public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH)
    blob = base64.b64decode(canonical.split()[1], validate=True)
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    return canonical.decode(), fingerprint


@contextmanager
def account_api(selection: KeyAccount) -> Iterator[httpx.Client]:
    # Pin credentials to the explicitly named account for the entire request sequence.
    rc, token = command(
        "/usr/bin/gh",
        "auth",
        "token",
        "--hostname",
        selection.host,
        "--user",
        selection.account,
        capture=True,
    )
    if rc or not token.strip() or not token.strip().isascii():
        raise ValueError("Sign into the selected account through GitHub CLI")
    base = (
        "https://api.github.com/"
        if selection.host == "github.com"
        else f"https://{selection.host}/api/v3/"
    )
    with httpx.Client(
        base_url=base,
        headers={"Authorization": "Bearer " + token.strip()},
        follow_redirects=False,
        trust_env=False,
        timeout=15,
    ) as api:
        raw = request(api, "GET", "user", 200)
        if (
            AccountIdentity.model_validate_json(raw).login.casefold()
            != selection.account.casefold()
        ):
            raise ValueError("The selected credential belongs to a different GitHub account")
        yield api


class AccountIdentity(BaseModel):
    login: str


def request(
    api: httpx.Client, method: str, endpoint: str, status: int, data: dict[str, str] | None = None
) -> bytes:
    with api.stream(method, endpoint, json=data) as response:
        if response.status_code != status:
            raise ValueError("GitHub rejected the SSH key request")
        content = bytearray()
        for block in response.iter_bytes(chunk_size=8192):
            content.extend(block)
            if len(content) > 512 * 1024:
                raise ValueError("GitHub key response exceeds the inspection limit")
        return bytes(content)


def keys(api: httpx.Client) -> list[RemoteKey]:
    result = []
    for number in range(1, 6):
        raw = request(api, "GET", f"user/keys?per_page=100&page={number}", 200)
        rows = TypeAdapter(list[RemoteKey]).validate_json(raw)
        result.extend(rows)
        if len(rows) < 100:
            return result
    raise ValueError("Account key inventory exceeds the review limit")


def identity(selection: KeyAccount, fingerprint: str) -> str:
    return f"{selection.host}/{selection.account.casefold()}/{fingerprint}"


def local_key(selection: PublicKey) -> tuple[str, str]:
    path = inside_home(selection.public_key)
    if path.suffix != ".pub":
        raise ValueError("Choose an OpenSSH .pub file")
    original = capture_receipt(path).data
    if len(original) > 16384 or b"\n" in original.rstrip(b"\n"):
        raise ValueError("Choose exactly one public key")
    return public_material(original.strip())


def access(selection: Probe) -> Plan:
    _, fingerprint = local_key(selection)
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "HOME": str(Path.home())}
    if "SSH_AUTH_SOCK" in os.environ:
        env["SSH_AUTH_SOCK"] = os.environ["SSH_AUTH_SOCK"]
    result = run_command(
        [
            "/usr/bin/ssh",
            "-F",
            "/dev/null",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UpdateHostKeys=no",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "PreferredAuthentications=publickey",
            "-o",
            "ConnectTimeout=6",
            "-i",
            str(selection.public_key),
            "-l",
            selection.login,
            "--",
            selection.host,
        ],
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=10,
        max_output_bytes=8192,
        check=False,
    )
    expected = (
        f"Hi {selection.account}! You've successfully authenticated, "
        "but GitHub does not provide shell access."
    )
    verified = result.returncode == 1 and expected.casefold() in {
        s.strip().casefold() for s in (result.stdout + "\n" + result.stderr).splitlines()
    }
    return Plan(
        outcome=Outcome.NO_CHANGES if verified else Outcome.NEEDS_USER_ACTION,
        summary=f"Verified SSH access for {selection.account} with {fingerprint}"
        if verified
        else "Verify the host fingerprint, SSH agent and selected account in your terminal.",
    )


def prepare(selection: Upload | Revoke) -> Plan:
    with account_api(selection) as api:
        inventory = keys(api)
    if isinstance(selection, Upload):
        _, fingerprint = local_key(selection)
        existing = next(
            (key for key in inventory if public_material(key.key.encode())[1] == fingerprint), None
        )
        row = Resource(
            kind="remote-key",
            identity=identity(selection, fingerprint),
            ownership="preexisting" if existing else "created",
            before=fingerprint if existing else None,
            after=fingerprint,
            remote_id=str(existing.id) if existing else None,
            cleanup=existing is None,
        )
        return Plan(
            outcome=Outcome.NO_CHANGES if existing else Outcome.READY,
            summary=(
                f"Upload public SSH key {fingerprint} to {selection.account} on {selection.host}"
            ),
            resources=[row],
        )
    matching = next((key for key in inventory if key.id == selection.key_id), None)
    if not matching:
        return Plan(outcome=Outcome.NO_CHANGES, summary="The reviewed remote key is absent")
    fp = public_material(matching.key.encode())[1]
    target = identity(selection, fp)
    if not any(
        r.kind == "remote-key"
        and r.ownership == "created"
        and r.cleanup
        and r.identity == target
        and r.remote_id == str(selection.key_id)
        and r.after == fp
        for r in owned_resources()
    ):
        return Plan(
            outcome=Outcome.BLOCKED,
            summary="The ledger does not record creation of this account key",
        )
    return Plan(
        summary=f"Revoke remote SSH key {selection.key_id} ({fp}) on {target}",
        warnings=["Devices using this key will lose Git SSH access."],
        resources=[
            Resource(
                kind="remote-key",
                identity=target,
                ownership="created",
                effect="remove",
                before=fp,
                remote_id=str(matching.id),
                cleanup=True,
            )
        ],
    )


def mutate(selection: Upload | Revoke, plan: Plan) -> Result:
    row = plan.resources[0]
    with account_api(selection) as api:
        if isinstance(selection, Upload):
            public, fingerprint = local_key(selection)
            if row.after != fingerprint:
                return Result(outcome=Outcome.BLOCKED, message="Selected public key changed")
            record = RemoteKey.model_validate_json(
                request(api, "POST", "user/keys", 201, {"key": public, "title": selection.title})
            )
            if public_material(record.key.encode())[1] != fingerprint:
                return Result(
                    outcome=Outcome.UNKNOWN_OUTCOME,
                    message="Remote key response does not match the submitted key",
                )
            try:
                confirmed = RemoteKey.model_validate_json(
                    request(api, "GET", f"user/keys/{record.id}", 200)
                )
                matched = (
                    confirmed.id == record.id
                    and public_material(confirmed.key.encode())[1] == fingerprint
                )
            except (ValueError, httpx.HTTPError):
                matched = False
            if not matched:
                return Result(
                    outcome=Outcome.UNKNOWN_OUTCOME,
                    message="GitHub has not confirmed the created key ID and fingerprint.",
                )
            resource = row.model_copy(update={"remote_id": str(record.id), "verified": True})
        else:
            endpoint = f"user/keys/{selection.key_id}"
            record = RemoteKey.model_validate_json(request(api, "GET", endpoint, 200))
            if (
                record.id != selection.key_id
                or row.before != public_material(record.key.encode())[1]
            ):
                return Result(outcome=Outcome.BLOCKED, message="The remote key identity changed")
            request(api, "DELETE", endpoint, 204)
            resource = row
    return Result(
        outcome=Outcome.CHANGED,
        message="GitHub accepted the reviewed key action",
        resources=[resource],
    )


def verify(selection: Upload | Revoke, plan: Plan) -> Result:
    row = plan.resources[0]
    with account_api(selection) as api:
        remote = keys(api)
    if isinstance(selection, Upload):
        matches = [key for key in remote if public_material(key.key.encode())[1] == row.after]
        if len(matches) == 1:
            row = row.model_copy(update={"remote_id": str(matches[0].id)})
            return Result(
                outcome=Outcome.COMPLETE,
                message="GitHub verified remote key ID " + str(matches[0].id),
                resources=[row],
            )
    elif all(key.id != selection.key_id for key in remote):
        return Result(
            outcome=Outcome.COMPLETE, message="Remote key removal verified", resources=[row]
        )
    return Result(outcome=Outcome.PARTIAL, message="Account key readback differs")


def owned_selections() -> list[Revoke]:
    selections = []
    for row in owned_resources():
        if row.kind != "remote-key" or not row.cleanup or row.ownership != "created":
            continue
        host, account, fingerprint = row.identity.split("/", 2)
        if not row.remote_id or row.after != fingerprint:
            raise ValueError("Review the incomplete ledger entry before removing a remote key")
        selections.append(Revoke(host=host, account=account, key_id=int(row.remote_id)))
    return sorted(selections, key=lambda s: (s.host, s.account, s.key_id))


def cleanup_plan(hosts: set[str]) -> Plan:
    resources = []
    for selection in owned_selections():
        if selection.host not in hosts:
            raise ValueError("Review the enterprise host before removing its keys")
        plan = prepare(selection)
        if plan.outcome == Outcome.BLOCKED:
            return plan
        if plan.outcome == Outcome.NO_CHANGES:
            raise ValueError("Reconcile the missing remote key with its ledger entry")
        resources.extend(plan.resources)
    return Plan(
        summary="Revoke the displayed managed SSH keys from their individual hosting accounts",
        resources=resources,
        warnings=["Devices using each displayed fingerprint will lose SSH access."],
        outcome=Outcome.READY if resources else Outcome.NO_CHANGES,
    )


def cleanup(plan: Plan, *, readback: bool = False) -> Result:
    verified: list[Resource] = []
    for selection, row in zip(owned_selections(), plan.resources, strict=True):
        if str(selection.key_id) != row.remote_id:
            raise ValueError("The remote identity changed")
        item = plan.model_copy(update={"resources": [row]})
        try:
            if not readback:
                result = mutate(selection, item)
                if result.outcome != Outcome.CHANGED:
                    raise ValueError("Remote key removal interrupted")
            result = verify(selection, item)
        except (ValueError, OSError, httpx.HTTPError):
            return Result(
                outcome=Outcome.PARTIAL, message="Inspect the remaining key IDs", resources=verified
            )
        if result.outcome != Outcome.COMPLETE:
            return Result(
                outcome=Outcome.PARTIAL,
                message="Remote key removal needs inspection",
                resources=verified,
            )
        verified.append(row.model_copy(update={"verified": True}))
    return Result(
        outcome=Outcome.COMPLETE if readback else Outcome.CHANGED,
        message="Verified removal of the reviewed remote SSH keys",
        resources=verified,
    )
