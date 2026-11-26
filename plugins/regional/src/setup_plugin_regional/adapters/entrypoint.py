"""Start the package-owned native operation after checking its installed path."""

import os
import sys
from pathlib import Path

from setup_system_sdk.files.native_tool import verify_resource

if __name__ == "__main__":
    if os.geteuid() != 0:
        raise ValueError("This operation requires native administrative authorization.")
    verify_resource(
        Path("/usr/lib/setup-system/libexec/platform"),
        Path(__file__),
        "plugins/regional/setup_plugin_regional/adapters/entrypoint.py",
    )
    sys.path.append(str(Path(__file__).parents[2]))
    from setup_plugin_regional.change import main

    main()
