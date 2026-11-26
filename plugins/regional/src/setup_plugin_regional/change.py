"""Revalidate one regional value at the installed privilege boundary."""

import os
import sys
from pathlib import Path

from setup_plugin_regional.adapters.native import (
    current_boot_identity,
    current_target,
    require_installed_resource,
)

from . import regional
from .models import Confirmation


def apply(change: Confirmation) -> None:
    if os.geteuid() != 0 or not current_target()[1]:
        raise ValueError("Regional changes require the installed Debian backend.")
    require_installed_resource(Path(__file__), "plugins/regional/setup_plugin_regional/change.py")
    if current_boot_identity() != change.boot:
        raise ValueError("The target rebooted since confirmation.")
    regional.apply(change.setting, change.value, change.before)


def main() -> None:
    data = sys.stdin.buffer.read(16385)
    if len(data) > 16384:
        sys.exit(2)
    apply(Confirmation.model_validate_json(data))
