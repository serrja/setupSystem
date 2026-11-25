"""Resolve the official Linux CLI archive and compare its published SHA-256."""

import base64
import hashlib
import io
import json
import platform
import re
import tarfile
from pathlib import Path

from pydantic import Field

from setup_system.infrastructure.downloads import GITHUB_RELEASE_HOSTS, DownloadPolicy, read_https
from setup_system.infrastructure.file_publication import observe
from setup_system.infrastructure.managed_files import apply_files
from setup_system.infrastructure.user_storage import inside_home
from setup_system_sdk import Inputs, Outcome, Plan, Resource, Result, owned_resources
from setup_system_sdk.apt import GithubRelease

REPOSITORY = "https://github.com/openai/codex"


class Artifact(Inputs):
    version: str
    member: str
    url: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    binary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def binary() -> Path:
    return inside_home(Path.home() / ".local/bin/codex")


def resolve() -> Artifact:
    arch = platform.machine()
    if platform.system() != "Linux" or arch not in {"aarch64", "x86_64"}:
        raise ValueError("No reviewed official CLI release for this platform")
    metadata = read_https(
        "https://api.github.com/repos/openai/codex/releases/latest",
        DownloadPolicy(frozenset({"api.github.com"}), 2 * 1024 * 1024, 15, follow_redirects=False),
    )
    release = GithubRelease.model_validate_json(metadata)
    match = re.fullmatch(r"rust-v([0-9]+\.[0-9]+\.[0-9]+)", release.tag_name)
    if not match or release.prerelease or release.draft or not release.published_at:
        raise ValueError("No compatible stable Codex release")
    name = f"codex-{arch}-unknown-linux-musl"

    def asset(suffix: str) -> tuple[str, str]:
        return release.asset("openai/codex", name + "." + suffix)

    url, digest = asset("tar.gz")
    evidence_url, evidence_digest = asset("sigstore")
    evidence = read_https(evidence_url, DownloadPolicy(GITHUB_RELEASE_HOSTS, 64 * 1024, 15))
    if hashlib.sha256(evidence).hexdigest() != evidence_digest:
        raise ValueError("Published binary digest evidence changed")
    entry = json.loads(
        base64.b64decode(json.loads(evidence)["rekorBundle"]["Payload"]["body"], validate=True)
    )
    if (
        entry.get("kind") != "hashedrekord"
        or entry["spec"]["data"]["hash"]["algorithm"] != "sha256"
    ):
        raise ValueError("Unknown upstream binary digest format")
    return Artifact(
        version=match[1],
        member=name,
        url=url,
        sha256=digest,
        binary_sha256=entry["spec"]["data"]["hash"]["value"],
    )


def plan(action: str, artifact: Artifact) -> Plan:
    path = binary()
    state = observe(path)
    before = state.sha256 if state else None
    owned = any(
        r.kind == "file"
        and r.identity == str(path)
        and r.ownership == "created"
        and r.after == before
        for r in owned_resources()
    )
    if before and before != artifact.binary_sha256 and (action != "update" or not owned):
        return Plan(
            outcome=Outcome.BLOCKED,
            summary="A different CLI already occupies the target. Review its installation source.",
        )
    return Plan(
        outcome=Outcome.NO_CHANGES if before == artifact.binary_sha256 else Outcome.READY,
        summary=(
            f"Install official Codex {artifact.version} ({artifact.member}). Archive SHA-256 "
            f"{artifact.sha256}."
        ),
        resources=[
            Resource(
                kind="file",
                identity=str(path),
                after=artifact.binary_sha256,
                before=before,
                ownership="created" if not state else "modified",
                cleanup=not state,
                permissions=0o700,
            )
        ],
    )


def install(action: str, reviewed: Plan) -> Result:
    artifact = resolve()
    if plan(action, artifact) != reviewed:
        return Result(outcome=Outcome.BLOCKED, message="CLI release or installed binary changed")
    archive = read_https(artifact.url, DownloadPolicy(GITHUB_RELEASE_HOSTS, 192 * 1024 * 1024, 30))
    if hashlib.sha256(archive).hexdigest() != artifact.sha256:
        raise ValueError("Codex archive checksum differs")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r|gz") as tar:
        row = next(iter(tar), None)
        if (
            row is None
            or row.name != artifact.member
            or not row.isfile()
            or row.size > 512 * 1024 * 1024
        ):
            raise ValueError("Unexpected official CLI archive member")
        stream = tar.extractfile(row)
        if stream is None:
            raise ValueError("CLI archive contains no regular program file")
        with stream:
            content = stream.read(row.size + 1)
        if len(content) != row.size or tar.next() is not None:
            raise ValueError("Unexpected additional archive payload")
    if hashlib.sha256(content).hexdigest() != artifact.binary_sha256:
        raise ValueError("Codex binary checksum differs from the published release")
    path = binary()
    backup = apply_files(
        {path: content}, modes={path: 0o700}, expected={path: reviewed.resources[0].before}
    )
    return Result(
        outcome=Outcome.CHANGED,
        message="Official CLI binary installed",
        resources=reviewed.resources,
        recovery_id=backup.name,
    )
