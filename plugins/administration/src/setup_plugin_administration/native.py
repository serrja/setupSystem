"""Start the package-owned native operation after checking its installed path."""

import os
import sys
from pathlib import Path

from setup_system.layout import require_installed_resource

if __name__ == "__main__":
    if os.geteuid() != 0:
        raise ValueError("This operation requires native administrative authorization.")
    require_installed_resource(
        Path(__file__), "plugins/administration/setup_plugin_administration/native.py"
    )
    sys.path.append(str(Path(__file__).parents[1]))
    from setup_plugin_administration.grant import main

    main()
