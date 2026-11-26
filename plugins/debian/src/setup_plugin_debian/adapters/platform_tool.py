"""Call only installed platform probes and reviewed resource checks."""

import json
import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import JsonValue, TypeAdapter

from setup_plugin_debian.adapters import apt, inspection, layout, native, platform


def main(args: list[str]) -> None:
    match args:
        case ["platform"]:
            result = platform.current_target()
        case ["boot"]:
            result = platform.current_boot_identity()
        case ["installed", name] if os.geteuid() == 0:
            result = apt.installed(TypeAdapter(apt.PackageName).validate_python(name))
        case ["inspect", home]:
            result = inspection.inspect_system(Path(home)).model_dump(mode="json")
        case ["observations", home]:
            result = inspection.observations(Path(home))
        case ["verify-resource", path, relative]:
            layout.require_installed_resource(Path(path), relative)
            result = True
        case ["play", plugin, recipe, method, descriptor]:
            auth = TypeAdapter(Literal["su", "sudo"]).validate_python(method)
            with os.fdopen(int(descriptor), "rb") as stream:
                payload = stream.read(65537)
            if len(payload) > 65536:
                raise ValueError("Oversized native request")
            native.run_native_play(
                plugin, recipe, TypeAdapter(dict[str, JsonValue]).validate_json(payload), auth
            )
            result = True
        case _:
            raise ValueError("Unknown platform request")
    print(json.dumps(result))


if __name__ == "__main__":
    main(sys.argv[1:])
