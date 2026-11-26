"""Call an installed, protected resource helper with bounded JSON arguments."""

import subprocess
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from setup_system_sdk.files.system_paths import protected
from setup_system_sdk.processes import run_command


def query(tool: Path, operation: str, *parameters: str) -> JsonValue:
    protected(tool, directory=False)
    result = run_command(
        [str(tool), operation, *parameters],
        cwd="/",
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
        stdin=subprocess.DEVNULL,
        discard_stderr=True,
        max_output_bytes=262144,
        timeout=60,
    )
    return TypeAdapter(JsonValue).validate_json(result.stdout)


def verify_resource(tool: Path, source: Path, relative: str) -> None:
    if query(tool, "verify-resource", str(source), relative) is not True:
        raise ValueError("The installed resource could not be verified")
