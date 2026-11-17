"""Apply portable user profiles independently of command-line presentation."""

from __future__ import annotations

import subprocess
from pathlib import Path

from setup_system.application.apps import catalog, install_zsh
from setup_system.application.configuration import apply_configuration
from setup_system.application.happ import format_happ_status, inspect_happ, setup_happ
from setup_system.application.interaction import ConsoleInteraction, Interaction
from setup_system.application.shell import configure_login_shell
from setup_system.application.terminal_dev import (
    install_archive_tool,
    install_language_tools,
    install_neovim,
    install_parsers,
    install_plugins,
    install_system_tools,
)
from setup_system.planning import Platform, UserProfile, create_plan, format_plan


class UserProfileService:
    """Own consent and ordered user operations while preserving their diagnostics."""

    def __init__(self, root: Path, *, interaction: Interaction | None = None) -> None:
        self.root = root
        self.interaction = ConsoleInteraction() if interaction is None else interaction

    def run(self, profile: str | UserProfile, *, yes: bool = False) -> int:
        try:
            selected = UserProfile(profile)
        except ValueError:
            raise ValueError("Unknown setup profile") from None
        plan = create_plan(self.root, selected)
        if not plan.platform.implemented or plan.blocked:
            self.interaction.write(format_plan(plan), error=True)
            return 4
        if not yes:
            self.interaction.write(format_plan(plan))
            if not self.interaction.confirm("Apply user configuration?"):
                return 2
        if selected == UserProfile.TERMINAL_DEV:
            try:
                self.interaction.write(install_zsh(catalog(self.root)["zsh"]))
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                self.interaction.write(f"Zsh installation blocked: {exc}", error=True)
                return 4
        try:
            backup, statuses = apply_configuration(
                self.root, terminal_dev=selected == UserProfile.TERMINAL_DEV
            )
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            self.interaction.write(f"failed: {exc}", error=True)
            return 5
        self.interaction.write(f"backup: {backup}")
        for path, status in statuses:
            self.interaction.write(f"{status}: {path}")
        if selected == UserProfile.TERMINAL_DEV:
            code = configure_login_shell(self.root, self.interaction)
            if code:
                return code
            return self._terminal_tools(plan.platform)
        if selected == UserProfile.HAPP:
            try:
                self.interaction.write(setup_happ(self.root, plan.platform))
                self.interaction.write(format_happ_status(inspect_happ(plan.platform)))
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                self.interaction.write(f"Happ setup failed: {exc}", error=True)
                return 5
        return 0

    def _terminal_tools(self, platform: Platform) -> int:
        try:
            self.interaction.write(install_neovim(self.root, platform))
            for status in install_plugins(self.root):
                self.interaction.write(status)
            for tool_name in ("stylua", "tree-sitter"):
                self.interaction.write(install_archive_tool(self.root, platform, tool_name))
            for status in install_language_tools(self.root):
                self.interaction.write(status)
            self.interaction.write(install_parsers(self.root))
            tool_status, blocked = install_system_tools(self.root, platform)
            self.interaction.write(tool_status)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            self.interaction.write(f"terminalDev failed: {exc}", error=True)
            return 5
        return 4 if blocked else 0
