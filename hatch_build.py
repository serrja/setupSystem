"""Attach independently built, offline plugin wheels to each product distribution."""

import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from hatchling.builders.config import BuilderConfig
from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class PluginCatalogHook(BuildHookInterface[BuilderConfig]):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if version == "editable":
            return
        self._catalog = TemporaryDirectory(prefix="setup-system-plugin-wheels-")
        root = Path(self.root)
        subprocess.run(
            [
                sys.executable,
                str(root / "docker/plugin_artifacts.py"),
                "--source-root",
                self.root,
                "--output",
                self._catalog.name,
            ],
            check=True,
        )
        destination = (
            "setup_system/bundle/plugin_catalog"
            if self.target_name == "wheel"
            else "plugin_catalog"
        )
        build_data["force_include"][self._catalog.name] = destination

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        if version != "editable":
            self._catalog.cleanup()
