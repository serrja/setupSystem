"""Access the installed platform integration using its protected tool."""

import subprocess
import tempfile
from pathlib import Path

from pydantic import JsonValue, TypeAdapter
from setup_system_sdk.files.system_paths import protected

TOOL = Path("/usr/lib/setup-system/libexec/platform")


def run_native_play(plugin: str, recipe: str, values: dict[str, JsonValue], method: str) -> None:
    protected(TOOL, directory=False)
    payload = TypeAdapter(dict[str, JsonValue]).dump_json(values)
    if len(payload) > 65536:
        raise ValueError("Native request exceeds its limit")
    with tempfile.TemporaryFile() as channel:
        channel.write(payload)
        channel.seek(0)
        subprocess.run(
            [str(TOOL), "play", plugin, recipe, method, str(channel.fileno())],
            pass_fds=(channel.fileno(),),
            check=True,
        )
