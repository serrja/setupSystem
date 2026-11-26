"""Recheck the installed package before a privileged native operation."""

import importlib.util
import os
import sys
from pathlib import Path

from setup_system.layout import require_installed_resource


def main() -> None:
    if os.geteuid() != 0:
        raise ValueError("This operation requires native administrative authorization.")
    folder = Path(__file__).parent
    require_installed_resource(Path(__file__), "plugins/greeter/setup_plugin_greeter/native.py")
    spec = importlib.util.spec_from_file_location(
        "setup_plugin_greeter", folder / "__init__.py", submodule_search_locations=[str(folder)]
    )
    if not spec or not spec.loader:
        raise ValueError("Package-owned implementation unavailable.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module = importlib.import_module(spec.name + ".plugin")
    raw = sys.stdin.buffer.read(262145)
    if len(raw) > 262144:
        raise ValueError("Request exceeds the native bound.")
    module.activate(module.Confirmation.model_validate_json(raw))


if __name__ == "__main__":
    main()
