"""Observed native program identity."""

from pathlib import Path

from pydantic import StrictBool

from setup_system.domain.contracts import ContractModel


class ShellStatus(ContractModel):
    """Observed host capability; no value is inferred from ``$SHELL``."""

    user: str
    home: Path
    startup_dir: Path
    login_shell: Path
    zsh: Path | None
    registered: StrictBool
