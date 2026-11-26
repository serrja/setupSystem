"""Execute the verified package-owned Snap command at the native privilege boundary."""

import asyncio
import importlib
import importlib.util
import os
import sys
from pathlib import Path

from setup_system.layout import require_installed_resource


def main() -> None:
    path = Path(__file__)
    require_installed_resource(path, "plugins/snap/setup_plugin_snap/privileged.py")
    if os.geteuid() != 0:
        raise ValueError("An authenticated administrator is required")
    spec = importlib.util.spec_from_file_location(
        "setup_plugin_snap",
        path.with_name("__init__.py"),
        submodule_search_locations=[str(path.parent)],
    )
    if spec is None or spec.loader is None:
        raise ValueError("The installed Snap package is incomplete")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    from setup_plugin_snap.actions import ApprovedChange, apply

    data = sys.stdin.buffer.read(65537)
    if len(data) > 65536:
        raise ValueError("The Snap approval exceeds its bound")
    asyncio.run(apply(ApprovedChange.model_validate_json(data).approval))


if __name__ == "__main__":
    main()
