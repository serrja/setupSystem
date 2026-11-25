"""Publish the reviewed user launcher through its fixed Make target."""

import sys

from setup_plugin_libreoffice import Plugin
from setup_system_sdk.native import recipe

if __name__ == "__main__":
    recipe(Plugin(), sys.argv[1])
