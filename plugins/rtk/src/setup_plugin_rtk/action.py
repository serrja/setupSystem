"""Run an accepted user action from the plugin Makefile."""

import sys

from setup_plugin_rtk.plugin import Plugin
from setup_system.execution.recipes.native import recipe

if __name__ == "__main__":
    recipe(Plugin(), sys.argv[1])
