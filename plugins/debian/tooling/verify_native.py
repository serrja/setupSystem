"""Inspect an assembled Debian archive in a private staging directory."""

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path


def verify(archive: Path) -> None:
    depends = subprocess.check_output(
        ["dpkg-deb", "-f", str(archive), "Depends"], text=True
    ).strip()
    if re.search(r"\b(?:python3-[a-z0-9-]+|ansible-core)\b", depends):
        raise ValueError("Python libraries must be supplied by the locked uv runtimes")
    with tempfile.TemporaryDirectory() as stage:
        root = Path(stage)
        subprocess.run(["dpkg-deb", "--extract", str(archive), str(root)], check=True)
        records = root / "etc/setup-system/executors"
        if not (records / "debian.json").is_file():
            raise ValueError("Missing package provider execution record")
        runtime = root / "usr/lib/setup-system"
        runtime_assets = runtime / "runtime"
        source = (runtime_assets / "source").read_text().strip()
        if not re.fullmatch(r"[a-f0-9]{40}", source):
            raise ValueError("Invalid package runtime source")
        for arch in ("amd64", "arm64"):
            uv = runtime_assets / "uv" / arch / "uv"
            with uv.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if (uv.parent / "uv.sha256").read_text() != f"{digest}  uv\n":
                raise ValueError("Bundled uv digest differs from its package receipt")
            wheels = {
                hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (runtime_assets / "wheelhouse" / arch).glob("*.whl")
            }
            for role in ("host", "native"):
                requirements = (runtime_assets / f"{role}-{arch}.txt").read_text()
                if not requirements.strip():
                    raise ValueError("Empty locked runtime closure")
                for line in requirements.splitlines():
                    match = re.fullmatch(
                        r"[a-z0-9][a-z0-9._-]*==[a-zA-Z0-9.+!-]+ --hash=sha256:([a-f0-9]{64})",
                        line,
                    )
                    if not match or match.group(1) not in wheels:
                        raise ValueError("Locked runtime wheel is missing or changed")
        for recipe in (runtime / "plugins").glob("*/*/resources/*.yml"):
            for relative in re.findall(
                r"/usr/lib/setup-system/(plugins/[A-Za-z0-9_./-]+\.py)\b", recipe.read_text()
            ):
                if ".." in Path(relative).parts or not (runtime / relative).is_file():
                    raise ValueError(f"Missing protected recipe executable in {recipe.name}")
        for record in records.glob("*.json"):
            entry = json.loads(record.read_bytes())
            for name, expected in {
                entry["launcher"]: entry["launcher_sha256"],
                **entry["resources"],
            }.items():
                path = Path(name)
                if not path.is_absolute() or ".." in path.parts:
                    raise ValueError("Invalid execution path")
                with root.joinpath(*path.parts[1:]).open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                        raise ValueError("Execution record differs from packaged contents")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    verify(parser.parse_args().archive)
