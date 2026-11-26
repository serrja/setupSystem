"""Run the installed, verified firefox recipe in the protected interpreter."""

import importlib.util
import runpy
import sys
from pathlib import Path

from setup_system_sdk.files.native_tool import verify_resource

if __name__ == "__main__":
    folder = Path(__file__).parent.parent
    verify_resource(
        Path("/usr/lib/setup-system/libexec/platform"),
        Path(__file__),
        "plugins/firefox/setup_plugin_firefox/adapters/entrypoint.py",
    )
    package = importlib.util.spec_from_file_location(
        "setup_plugin_firefox", folder / "__init__.py", submodule_search_locations=[str(folder)]
    )
    if package is None or package.loader is None:
        raise SystemExit(2)
    module = importlib.util.module_from_spec(package)
    sys.modules[package.name] = module
    package.loader.exec_module(module)
    runpy.run_module(package.name + ".policy", run_name="__main__")
