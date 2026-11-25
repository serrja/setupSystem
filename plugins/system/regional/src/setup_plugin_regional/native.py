"""Start the package-owned native operation after checking its installed path."""

import os
import sys
from pathlib import Path

from setup_system.layout import require_installed_resource

if __name__ == "__main__":
    if os.geteuid() != 0:
        raise ValueError("This operation requires native administrative authorization.")
    require_installed_resource(Path(__file__), "plugins/regional/setup_plugin_regional/native.py")
    sys.path.append(str(Path(__file__).parents[1]))
    from setup_plugin_regional.change import main

    main()
