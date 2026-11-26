"""Inspect an assembled Debian archive in a private staging directory."""

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path


def verify(archive: Path) -> None:
    with tempfile.TemporaryDirectory() as stage:
        root = Path(stage)
        subprocess.run(["dpkg-deb", "--extract", str(archive), str(root)], check=True)
        records = root / "etc/setup-system/executors"
        if not (records / "debian.json").is_file():
            raise ValueError("Missing package provider execution record")
        runtime = root / "usr/lib/setup-system"
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
