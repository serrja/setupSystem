"""Observed native program identity."""

from pathlib import Path

from pydantic import StrictBool

from setup_system.execution.contracts import ContractModel


class RtkStatus(ContractModel):
    """Observed binary identity and local installation path."""

    path: Path | None
    version: str | None
    product_matches: StrictBool

    def describe(self, selected_version: str) -> str:
        if self.path is not None and not self.product_matches:
            return "blocked: command named rtk is not rtk-ai/rtk"
        if self.version == selected_version:
            return "unchanged: selected RTK is already available"
        if self.path is not None:
            return f"update available: managed RTK → {selected_version}"
        return f"install available: selected {selected_version}"
