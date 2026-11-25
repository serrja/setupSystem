"""Build independently installable plugin wheels and release metadata from trusted source."""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile


def build_wheel(source: Path, destination: Path, python: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(python),
            "-c",
            "import hatchling.build,sys; hatchling.build.build_wheel(sys.argv[1])",
            str(destination.resolve()),
        ],
        cwd=source,
        env=dict(os.environ, SOURCE_DATE_EPOCH="315532800"),
        check=True,
    )
    candidates = list(destination.glob("*.whl"))
    if len(candidates) != 1:
        raise ValueError("PEP 517 did not produce one wheel")
    return candidates[0]


def runtime_wheels(
    source: Path, stage: Path, output: Path, python: Path
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Materialize uv's locked runtime selection for this interpreter, verifying each archive."""
    from packaging.markers import Marker
    from packaging.tags import sys_tags
    from packaging.utils import parse_wheel_filename

    from setup_system.infrastructure.downloads import DownloadPolicy, download_https

    if not python.samefile(sys.executable):
        raise ValueError("Run the runtime-wheel builder with the target interpreter")
    runtime = stage / "runtime"
    runtime.mkdir()
    core = build_wheel(source, runtime, python)
    with core.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    wheels = [{"name": core.name, "sha256": digest, "url": "runtime/" + core.name}]
    args = [
        "uv",
        "export",
        "--locked",
        "--no-dev",
        "--no-emit-project",
        "--offline",
        "--format",
        "pylock.toml",
        "--quiet",
        "--project",
        str(source),
    ]
    base_lock = subprocess.check_output(args)
    base_names = {
        p["name"]
        for p in tomllib.loads(base_lock.decode())["packages"]
        if "marker" not in p or Marker(p["marker"]).evaluate()
    }
    lock = subprocess.check_output([*args, "--group", "plugin-runtime"])
    ranks = {tag: i for i, tag in enumerate(sys_tags())}
    sources: list[tuple[str, str, int, str]] = []
    for package in tomllib.loads(lock.decode())["packages"]:
        if "marker" in package and not Marker(package["marker"]).evaluate():
            continue
        if package.get("index") != "https://pypi.org/simple":
            raise ValueError("Runtime wheel source is outside the locked package index")
        compatible = []
        for wheel in package.get("wheels", []):
            url = wheel["url"]
            if urlsplit(url).netloc != "files.pythonhosted.org":
                raise ValueError("Unexpected runtime wheel origin")
            name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
            dist, version, _, tags = parse_wheel_filename(name)
            if dist != package["name"] or str(version) != package["version"]:
                raise ValueError("Locked wheel name disagrees with selected version")
            rank = min((ranks[t] for t in tags if t in ranks), default=None)
            if rank is not None:
                compatible.append((rank, name, url, wheel["size"], wheel["hashes"]["sha256"]))
        if not compatible:
            raise ValueError("No compatible binary wheel for " + package["name"])
        _, name, url, size, expected = min(compatible)
        sources.append((name, url, size, expected))
        wheels.append({"name": name, "sha256": expected, "url": "runtime/" + name})

    async def fetch() -> None:
        limit = asyncio.Semaphore(5)

        async def one(name: str, url: str, size: int, expected: str) -> None:
            cached = output / "runtime" / name
            if cached.is_file() and not cached.is_symlink():
                with cached.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() == expected:
                        shutil.copyfile(cached, runtime / name)
                        return
            if not 0 < size <= 128 * 1024 * 1024:
                raise ValueError("Locked runtime wheel exceeds the archive limit")
            async with limit:
                data = await download_https(
                    url,
                    DownloadPolicy(
                        frozenset({"files.pythonhosted.org"}), size, 30, 120, follow_redirects=False
                    ),
                )
                if len(data) != size or hashlib.sha256(data).hexdigest() != expected:
                    raise ValueError("Downloaded runtime wheel failed its lock hash")
                (runtime / name).write_bytes(data)

        async with asyncio.TaskGroup() as tasks:
            for item in sources:
                tasks.create_task(one(*item))

    asyncio.run(fetch())
    base = [
        w
        for w in wheels
        if w["name"] == core.name or parse_wheel_filename(w["name"])[0] in base_names
    ]
    return base, wheels


def build(
    source_root: Path, output: Path, python: Path, *, with_runtime: bool = False
) -> list[Path]:
    """Use each package's declared PEP 517 backend and publish its validated wheel last."""
    projects = sorted((source_root / "plugins").glob("*/*/pyproject.toml"))
    if not projects:
        raise ValueError("No plugin distributions found")
    output.mkdir(parents=True, exist_ok=True)
    ids: set[str] = set()
    files: list[Path] = []
    with tempfile.TemporaryDirectory(prefix=".build-plugins-", dir=output) as temporary:
        staging = Path(temporary)
        runtime = runtime_wheels(source_root, staging, output, python) if with_runtime else ([], [])
        for project in projects:
            config = tomllib.loads(project.read_text())["project"]
            registry = config["entry-points"]["setup_system.plugins"]
            if len(registry) != 1:
                raise ValueError("Each plugin distribution needs one named backend")
            plugin_id, target = next(iter(registry.items()))
            module = target.partition(":")[0]
            if (
                plugin_id != project.parent.name
                or not module.isidentifier()
                or plugin_id in ids
                or not plugin_id.isascii()
            ):
                raise ValueError("Invalid or duplicate build identity")
            ids.add(plugin_id)
            destination = staging / plugin_id
            wheel = build_wheel(project.parent, destination, python)
            with ZipFile(wheel) as archive:
                manifest = json.loads(archive.read(f"{module}/manifest.json"))
                if (
                    archive.testzip()
                    or manifest["id"] != plugin_id
                    or (
                        manifest["entry_point"] != target
                        or manifest["version"] != config["version"]
                    )
                ):
                    raise ValueError("Built wheel no longer matches its project declaration")
            with wheel.open("rb") as archive_file:
                digest = hashlib.file_digest(archive_file, "sha256").hexdigest()
            record = {
                "manifest": manifest,
                "wheels": [
                    {"name": wheel.name, "sha256": digest},
                    *(runtime[1] if config.get("optional-dependencies") else runtime[0]),
                ],
            }
            document = {"format": 1, "releases": [record]}
            release = destination / f"{plugin_id}.json"
            release.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
            files.extend([release, wheel])
        if with_runtime:
            dest = output / "runtime"
            dest.mkdir(exist_ok=True)
            for path in (staging / "runtime").iterdir():
                path.replace(dest / path.name)
        for path in files:
            path.replace(output / path.name)
    return [output / p.name for p in files]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument(
        "--with-runtime",
        action="store_true",
        help="Include the locked wheel closure for development or offline use on this interpreter",
    )
    options = parser.parse_args()
    for path in build(
        options.source_root.resolve(),
        options.output.resolve(),
        options.python,
        with_runtime=options.with_runtime,
    ):
        print(path.name)


if __name__ == "__main__":
    main()
