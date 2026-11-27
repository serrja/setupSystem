"""Create a release descriptor for a primary wheel and its compatible locked wheels."""

import hashlib
import json
import os
import tempfile
import tomllib
import zipfile
from collections import deque
from email.parser import BytesParser
from pathlib import Path
from typing import Annotated
from urllib.parse import unquote, urlsplit

import anyio
import httpx
import typer
from packaging.markers import Environment, default_environment
from packaging.requirements import Requirement
from packaging.tags import Tag, sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version
from setup_system_sdk.downloads import DownloadClient, DownloadPolicy
from setup_system_sdk.manifests import Manifest


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


async def locked_closure(
    primary: Path,
    sdk: Path,
    lock: Path,
    out: Path,
    *,
    target_tags: tuple[Tag, ...] | None = None,
    environment: Environment | None = None,
) -> list[dict[str, str]]:
    projects = {p["name"]: p for p in tomllib.loads(lock.read_text())["package"]}
    local = {parse_wheel_filename(p.name)[0]: p for p in (primary, sdk)}
    for name, project in projects.items():
        if name not in local and "editable" in project.get("source", {}):
            matches = [
                p
                for p in sdk.parent.glob("*.whl")
                if parse_wheel_filename(p.name)[:2] == (name, Version(project["version"]))
            ]
            if len(matches) == 1:
                local[name] = matches[0]
    out.mkdir(parents=True, exist_ok=True)
    ranks = {tag: index for index, tag in enumerate(target_tags or sys_tags())}
    pending = deque([(parse_wheel_filename(primary.name)[0], frozenset([""]))])
    scanned: set[tuple[str, frozenset[str]]] = set()
    reviewed: dict[str, tuple[Path, str]] = {}
    async with httpx.AsyncClient(trust_env=False) as http:
        client = DownloadClient(http)
        while pending:
            name, extras = pending.popleft()
            if (name, extras) in scanned:
                continue
            scanned.add((name, extras))
            if name not in reviewed:
                if name in local:
                    source = local[name]
                    path = out / source.name
                    expected = digest(source)
                    publish(path, source.read_bytes(), expected)
                else:
                    compatible = []
                    for wheel in projects[name].get("wheels", []):
                        filename = unquote(Path(urlsplit(wheel["url"]).path).name)
                        wheel_name, version, _, tags = parse_wheel_filename(filename)
                        if str(version) != projects[name]["version"] or wheel_name != name:
                            raise ValueError("Lockfile wheel identity differs")
                        matching = [ranks[t] for t in tags if t in ranks]
                        if matching:
                            compatible.append((min(matching), filename, wheel))
                    if not compatible:
                        raise ValueError(f"No locked wheel for {name} on this interpreter")
                    _, filename, wheel = min(compatible, key=lambda w: (w[0], w[1]))
                    expected = wheel["hash"].removeprefix("sha256:")
                    path = out / filename
                    if not path.exists():
                        policy = DownloadPolicy(
                            frozenset({"files.pythonhosted.org"}), wheel["size"], 30
                        )
                        publish(path, await client.read(wheel["url"], policy, expected), expected)
                    elif digest(path) != expected:
                        raise ValueError("Existing wheel does not match uv.lock")
                reviewed[name] = (path, expected)
            path, _ = reviewed[name]
            with zipfile.ZipFile(path) as dist:
                (metadata,) = [f for f in dist.namelist() if f.endswith(".dist-info/METADATA")]
                headers = BytesParser().parsebytes(dist.read(metadata), headersonly=True)
            for value in headers.get_all("Requires-Dist", []):
                dep = Requirement(value)
                if dep.marker:
                    applies = False
                    for extra in extras | {""}:
                        marker_environment = {
                            key: str(value)
                            for key, value in (
                                environment if environment is not None else default_environment()
                            ).items()
                        }
                        marker_environment["extra"] = extra
                        if dep.marker.evaluate(marker_environment):
                            applies = True
                            break
                    if not applies:
                        continue
                dependency = canonicalize_name(dep.name)
                if (
                    dep.url
                    or dependency not in projects
                    or projects[dependency]["version"] not in dep.specifier
                ):
                    raise ValueError(f"Incompatible or unpinned dependency {dep.name}")
                pending.append((dependency, frozenset(dep.extras)))
    return [{"name": path.name, "sha256": expected} for path, expected in reviewed.values()]


async def generate(
    primary: Path,
    sdk: Path,
    lock: Path,
    out: Path,
    *,
    target_tags: tuple[Tag, ...] | None = None,
    environment: Environment | None = None,
) -> dict[str, object]:
    wheels = await locked_closure(
        primary, sdk, lock, out, target_tags=target_tags, environment=environment
    )
    with zipfile.ZipFile(primary) as wheel:
        (member,) = [
            n for n in wheel.namelist() if len(Path(n).parts) == 2 and n.endswith("/manifest.json")
        ]
        manifest = Manifest.model_validate_json(wheel.read(member))
    return {
        "manifest": manifest.model_dump(mode="json"),
        "wheels": wheels,
    }


def descriptor(path: Path, records: list[dict[str, object]]) -> None:
    document = (json.dumps({"format": 1, "releases": records}, indent=2) + "\n").encode()
    publish(path, document, hashlib.sha256(document).hexdigest())


async def emit(primary: Path, sdk: Path, lock: Path, out: Path) -> None:
    record = await generate(primary, sdk, lock, out)
    manifest = Manifest.model_validate(record["manifest"])
    descriptor(out / (manifest.id + ".json"), [record])


def publish(path: Path, content: bytes, expected: str) -> None:
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError("Release artifact digest mismatch")
    if path.exists():
        if digest(path) != expected:
            raise ValueError("An artifact with this name already has different bytes")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent) as staged:
        staged.write(content)
        staged.flush()
        os.fsync(staged.fileno())
        os.link(staged.name, path)


def main(
    primary: Annotated[Path, typer.Option("--primary")],
    sdk: Annotated[Path, typer.Option("--sdk")],
    output: Annotated[Path, typer.Option("--output")],
    lock: Annotated[Path, typer.Option("--lock")] = Path("uv.lock"),
) -> None:
    """Write a reviewed plugin release descriptor and its locked wheel closure."""
    anyio.run(emit, primary, sdk, lock, output, backend="asyncio")


if __name__ == "__main__":
    typer.run(main)
