"""Run one reviewed normal-user recipe."""

import sys

from setup_plugin_docker.plugin import Plugin
from setup_system.execution.recipes.native import recipe

if __name__ == "__main__":
    recipe(Plugin(), sys.argv[1])
