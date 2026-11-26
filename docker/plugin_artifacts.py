"""Create a release descriptor for a primary wheel and its compatible locked wheels."""

import argparse
import hashlib
import json
import os
import tempfile
import tomllib
import zipfile
from collections import deque
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import anyio
import httpx
from packaging.markers import Environment
from packaging.requirements import Requirement
from packaging.tags import Tag, sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from setup_system_sdk.downloads import DownloadClient, DownloadPolicy
from setup_system_sdk.manifests import Manifest


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


async def generate(
    primary: Path,
    sdk: Path,
    lock: Path,
    out: Path,
    *,
    target_tags: tuple[Tag, ...] | None = None,
    environment: Environment | None = None,
) -> dict[str, object]:
    projects = {p["name"]: p for p in tomllib.loads(lock.read_text())["package"]}
    local = {parse_wheel_filename(p.name)[0]: p for p in (primary, sdk)}
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
                    for wheel in projects[name]["wheels"]:
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
                if dep.marker and not any(
                    dep.marker.evaluate({**(environment or {}), "extra": extra})
                    for extra in extras | {""}
                ):
                    continue
                dependency = canonicalize_name(dep.name)
                if (
                    dep.url
                    or dependency not in projects
                    or projects[dependency]["version"] not in dep.specifier
                ):
                    raise ValueError(f"Incompatible or unpinned dependency {dep.name}")
                pending.append((dependency, frozenset(dep.extras)))
    with zipfile.ZipFile(primary) as wheel:
        (member,) = [
            n for n in wheel.namelist() if len(Path(n).parts) == 2 and n.endswith("/manifest.json")
        ]
        manifest = Manifest.model_validate_json(wheel.read(member))
    return {
        "manifest": manifest.model_dump(mode="json"),
        "wheels": [{"name": path.name, "sha256": expected} for path, expected in reviewed.values()],
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("primary", "sdk", "output"):
        parser.add_argument("--" + option, type=Path, required=True)
    parser.add_argument("--lock", type=Path, default=Path("uv.lock"))
    args = parser.parse_args()
    anyio.run(emit, args.primary, args.sdk, args.lock, args.output, backend="asyncio")
