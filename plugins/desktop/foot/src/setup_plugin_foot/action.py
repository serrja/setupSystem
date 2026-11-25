"""Apply one confirmed user configuration action."""

import sys

from setup_plugin_foot import Plugin
from setup_system_sdk.native import recipe

if __name__ == "__main__" and len(sys.argv) == 2:
    recipe(Plugin(), sys.argv[1])
