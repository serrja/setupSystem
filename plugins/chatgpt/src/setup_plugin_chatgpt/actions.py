"""Manage ChatGPT through its supported Debian package and native interface."""

from enum import StrEnum
from importlib.resources import files as package_files
from pathlib import Path
from typing import ClassVar

from setup_system_sdk.files.backend import NativeBackend

from setup_plugin_chatgpt.enums import PluginAction


class PluginActions(NativeBackend):
    executable = "/usr/bin/chatgpt"

    package_folder = Path(str(package_files("setup_plugin_chatgpt").joinpath("resources")))

    manual: ClassVar[dict[StrEnum, str]] = {
        PluginAction.ACCOUNT: "Sign in from the official ChatGPT account dialog.",
        PluginAction.SETTINGS: "Open ChatGPT Settings for personalization and account preferences.",
    }
