"""App-owned plan/apply service; viewing and saving intentions never elevate privileges."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from threading import Event, Lock
from typing import Protocol

from setup_system.application.interaction import ConsoleInteraction, Interaction
from setup_system.diagnostics import error_text
from setup_system.domain.operations import Action, WorkstationPlan
from setup_system.domain.settings import Section, SystemSettings, UserSettings
from setup_system.infrastructure.config_store import ConfigStore, SettingsPaths
from setup_system.infrastructure.privileged import PrivilegedExecutor
from setup_system.infrastructure.theme import configure_targets, preview, render
from setup_system.infrastructure.user_files import apply_files
from setup_system.installation import TargetUser


class Operation(StrEnum):
    PLAN = "plan"
    APPLY = "apply"


@dataclass(frozen=True)
class InstallRequest:
    operation: Operation
    user: str | None = None
    yes: bool = False
    overrides: dict[str, Path] = field(default_factory=dict)
    timezone: str | None = None
    section: Section = Section.BASE
    system: SystemSettings = field(default_factory=SystemSettings)
    settings: UserSettings = field(default_factory=UserSettings)


class Executor(Protocol):
    def run(self, action: str, settings: dict[str, object]) -> None: ...


class InstallationService:
    """Keep one immutable running plan across navigation, with boundary cancellation."""

    def __init__(
        self,
        root: Path,
        *,
        interaction: Interaction | None = None,
        executor: Executor | None = None,
        authorize: Callable[[], bool] | None = None,
        identity: Callable[[str | None], TargetUser] | None = None,
    ) -> None:
        self.root = root
        self.identity = identity
        self.interaction = ConsoleInteraction() if interaction is None else interaction
        self.paths = SettingsPaths()
        self.cancel = Event()
        self._mutation = Lock()
        self.completed: list[str] = []
        self.authorize = authorize
        self.executor = executor
        if executor is None:
            marker = self.paths.state / "release.json"
            if marker.is_file() and not marker.is_symlink() and marker.stat().st_size < 8192:
                record = json.loads(marker.read_text())
                self.executor = PrivilegedExecutor(record["release"], record["source_sha"])

    def plan(self, request: InstallRequest) -> WorkstationPlan:

        from setup_system import installation as system
        from setup_system.infrastructure.inspection import observations

        target = (
            system.resolve_target(request.user)
            if self.identity is None
            else self.identity(request.user)
        )
        overrides = {
            kind: Path(os.environ[f"XDG_{kind.upper()}_HOME"])
            for kind in ("config", "data", "state", "cache")
            if f"XDG_{kind.upper()}_HOME" in os.environ
        }
        overrides.update(request.overrides)
        environment = system.user_environment(target, overrides)
        from setup_system.system_inspection import validate_timezone

        validate_timezone(request.system.timezone)
        revision = None
        if request.section == Section.BASE:
            revision = ConfigStore(self.paths.system, SystemSettings, uid=0).read()[1]
        release = platform.freedesktop_os_release()
        supported = (
            release.get("ID") == "debian"
            and release.get("VERSION_ID") == "13"
            and platform.machine() in {"x86_64", "aarch64", "arm64"}
        )
        blocked: tuple[str, ...] = (
            () if supported else ("This action requires Debian 13 (trixie), amd64/arm64",)
        )
        if self.executor is None:
            blocked += ("System apply requires a published release with native build provenance",)
        actions: list[Action] = []
        if request.section == Section.BASE:
            if request.system.hyprland:
                from setup_system.adapters.debian.hyprland import package_arguments

                try:
                    packages = package_arguments(
                        root=self.root, graphical_login=request.system.graphical_login
                    )
                except ValueError as exc:
                    packages = []
                    blocked += (str(exc),)
                actions.append(
                    Action(
                        "base",
                        "Signed official Debian/backports packages: " + " ".join(packages),
                        True,
                        blocked,
                    )
                )
                actions.append(
                    Action("desktop_files", "User Hyprland, portals, fonts and shared theme")
                )
            if request.system.graphical_login and not request.system.hyprland:
                actions.append(
                    Action(
                        "login", "Graphical login", True, ("Select Hyprland before applying login",)
                    )
                )
            if request.system.plymouth:
                actions.append(
                    Action(
                        "plymouth",
                        "Native encrypted-disk unlock theme",
                        True,
                        (
                            "Initramfs/bootloader recovery requires a validated disposable target; "
                            "existing unlock remains unchanged",
                        ),
                    )
                )
        else:
            actions.append(Action("theme", preview(request.settings.theme)))
            workspace_packages = self.workspace_packages(request.settings)
            if workspace_packages or request.settings.happ:
                actions.append(
                    Action(
                        "workspace_packages",
                        "Official Debian packages: " + " ".join(workspace_packages),
                        True,
                        blocked,
                    )
                )
            if request.settings.neovim:
                actions.append(
                    Action(
                        "neovim", "Pinned Neovim and existing lazy/blink/LSP/editor configuration"
                    )
                )
            if request.settings.firefox:
                actions.append(
                    Action(
                        "firefox",
                        "Firefox Strict tracking protection, HTTPS-Only and uBlock Origin",
                    )
                )
            if request.settings.codex:
                actions.append(
                    Action(
                        "codex",
                        "Pinned official Codex CLI; preserve model and native authorization",
                    )
                )
            if request.settings.chatgpt:
                actions.append(
                    Action(
                        "chatgpt",
                        "Official ChatGPT Desktop",
                        True,
                        (
                            "UNAVAILABLE: official Linux download returned HTTP 403; "
                            "version and checksum could not be verified",
                        ),
                    )
                )
            for name in request.settings.optional_apps:
                actions.append(
                    Action(
                        name,
                        "Optional application",
                        False,
                        ("UNAVAILABLE: no verified package adapter for " + name,),
                    )
                )
            if "python" in request.settings.language_profiles:
                actions.append(Action("python_tools", "Hash-locked Ruff and one basedpyright LSP"))
            for profile in request.settings.language_profiles:
                if profile != "python":
                    actions.append(
                        Action(
                            "languages",
                            profile,
                            False,
                            ("UNAVAILABLE: this language profile has no validated delivery lock",),
                        )
                    )
            if request.settings.cleanup:
                actions.append(Action("cleanup", "Explicit package removal only", True, blocked))
        return WorkstationPlan(
            request.section,
            request.system,
            request.settings,
            tuple(actions),
            (
                f"Machine: {release.get('PRETTY_NAME', 'unknown')} / {platform.machine()}",
                "Network, SSH, existing sessions, disks and native password prompts are preserved.",
                *observations(target.home),
                "GPU, monitor/HiDPI, boot/unlock and GUI success require a real target smoke test.",
            ),
            environment=tuple(
                (kind, environment[f"XDG_{kind.upper()}_HOME"])
                for kind in ("config", "data", "state", "cache")
            ),
            system_revision=revision,
        )

    @staticmethod
    def workspace_packages(settings: UserSettings) -> tuple[str, ...]:
        packages: list[str] = []
        if settings.firefox:
            packages.append("firefox-esr")
        if settings.developer_tools:
            packages.extend(("git", "gh", "zsh", "ripgrep", "fzf"))
        if settings.neovim:
            packages.append("git")
        return tuple(dict.fromkeys(packages))

    def show_plan(self, plan: WorkstationPlan) -> None:
        for line in plan.describe():
            self.interaction.write(line)

    def run(self, request: InstallRequest) -> int:
        try:
            plan = self.plan(request)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            self.interaction.write(
                "Preflight failed; no operation started: " + error_text(exc), error=True
            )
            return 2
        try:
            self.show_plan(plan)
            if request.operation == Operation.PLAN:
                return 4 if plan.blockers else 0
            if not self._mutation.acquire(blocking=False):
                self.interaction.write("Another mutating operation is already running")
                return 2
            try:
                self.cancel.clear()
                self.completed = []
                if plan.blockers:
                    return 4
                if not request.yes and not self.interaction.confirm(
                    "Apply this fixed plan snapshot?\n" + "\n".join(plan.describe())
                ):
                    return 2
                if any(action.privileged for action in plan.actions):
                    if self.authorize is None or not self.authorize():
                        self.interaction.write("Privilege authorization was not granted")
                        return 2
                for action in plan.actions:
                    if self.cancel.is_set() or self.interaction.cancelled:
                        self.interaction.write(
                            "CANCELLED at a safe boundary; completed: " + ", ".join(self.completed)
                        )
                        return 3
                    self.interaction.write("Running: " + action.id)
                    self._apply(action.id, plan)
                    self.completed.append(action.id)
                self.interaction.write("Complete; no reboot or graphical session was started")
                return 0
            finally:
                self._mutation.release()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            self.interaction.write(
                "PARTIAL/FAILED: " + error_text(exc) + "; completed: " + ", ".join(self.completed),
                error=True,
            )
            return 5

    def _apply(self, action: str, plan: WorkstationPlan) -> None:
        if action == "base":
            assert self.executor is not None
            self.executor.run(
                "base",
                {
                    **plan.system.model_dump(mode="json"),
                    "xdg": dict(plan.environment),
                    "expected_system_sha256": plan.system_revision,
                },
            )
        elif action == "workspace_packages":
            assert self.executor is not None
            self.executor.run(
                "workspace_packages",
                {"packages": list(self.workspace_packages(plan.user)), "happ": plan.user.happ},
            )
        elif action == "desktop_files":
            from setup_system.application import workstation

            workstation.apply(
                self.root, "hyprland", theme=plan.user.theme, keyboard=plan.system.keyboard
            )
        elif action == "theme":
            from setup_system.application.workstation import _targets

            apply_files(
                configure_targets(
                    _targets(self.root, "hyprland"), plan.user.theme, keyboard=plan.system.keyboard
                )
            )
        elif action == "neovim":
            from setup_system.application.terminal_dev import install_neovim, install_plugins
            from setup_system.planning import detect_platform

            install_neovim(self.root, detect_platform())
            files = {
                self.paths.user.parent.parent
                / "nvim"
                / p.relative_to(self.root / "assets/nvim"): render(p.read_text()).encode()
                for p in (self.root / "assets/nvim").rglob("*")
                if p.is_file()
            }
            files[self.paths.user.parent / "editor.json"] = b'{"EDITOR":"nvim","VISUAL":"nvim"}\n'
            files[self.paths.user.parent.parent / "environment.d/90-setup-system-editor.conf"] = (
                b"EDITOR=nvim\nVISUAL=nvim\nGIT_EDITOR=nvim\n"
            )
            apply_files(files)
            install_plugins(self.root)
            os.environ.update(EDITOR="nvim", VISUAL="nvim", GIT_EDITOR="nvim")
        elif action == "python_tools":
            from setup_system.infrastructure.language_tools import install_python_tools

            install_python_tools()
        elif action == "firefox":
            from setup_system.infrastructure.app_configs import configure_firefox

            configure_firefox()
        elif action == "codex":
            from setup_system.infrastructure.app_configs import configure_codex

            configure_codex(self.root)
        elif action == "cleanup":
            import shutil

            from setup_system.infrastructure.cleanup import preview as cleanup_preview

            editor_verified = (
                os.environ.get("EDITOR") == "nvim"
                and os.environ.get("VISUAL") == "nvim"
                and shutil.which("nvim") is not None
            )
            if editor_verified:
                editor_verified = (
                    subprocess.run(
                        ["nvim", "--clean", "--headless", "+q"],
                        capture_output=True,
                        check=False,
                        timeout=30,
                    ).returncode
                    == 0
                )
            report = cleanup_preview(plan.user.cleanup, editor_verified=editor_verified)
            self.interaction.write(report.simulation)
            if not report.requested:
                return
            if not self.interaction.confirm(
                "Remove exactly these packages: "
                + ", ".join(sorted(report.removed))
                + "?\n"
                + report.simulation
            ):
                raise ValueError("Package removal cancelled")
            assert self.executor is not None
            self.executor.run(
                "cleanup",
                {
                    "packages": sorted(report.requested),
                    "mode": plan.user.cleanup_mode,
                    "closure": sorted(report.removed),
                    "xdg": dict(plan.environment),
                },
            )
        else:
            raise ValueError("No verified adapter for action: " + action)

    def save_user(self, settings: UserSettings, expected: str | None) -> str:
        return ConfigStore(self.paths.user, UserSettings).save(settings, expected)
