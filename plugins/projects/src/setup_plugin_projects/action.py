"""Apply a confirmed project action as the local user."""

import sys

from setup_plugin_projects.plugin import Plugin
from setup_system.execution.recipes.native import recipe

if __name__ == "__main__" and len(sys.argv) == 2:
    recipe(Plugin(), sys.argv[1])
