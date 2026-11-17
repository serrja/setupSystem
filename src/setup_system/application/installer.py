"""One installation use case shared by Textual, Typer and private user workers."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from setup_system import installation as system
from setup_system.application.interaction import ConsoleInteraction, Interaction
from setup_system.desktop_system import DesktopPlan, DesktopSystem
from setup_system.diagnostics import error_text
from setup_system.installation_state import InstallStage
from setup_system.network_session import NetworkReady, TemporaryNetwork
from setup_system.runtime import RUNTIME_PYTHON, UV_VERSION, UserRuntime
from setup_system.system_inspection import validate_home_access, validate_timezone


class Operation(StrEnum):
    PLAN = "plan"
    APPLY = "apply"
    NETWORK_STAGE = "network-stage"
    PREPARE_USER = "prepare-user"
    USER_STAGE = "user-stage"


@dataclass(frozen=True)
class InstallRequest:
    operation: Operation
    user: str | None = None
    yes: bool = False
    overrides: dict[str, Path] = field(default_factory=dict)
    subscription_file: Path | None = None
    timezone: str | None = None


@dataclass(frozen=True)
class InstallPlan:
    target: system.TargetUser
    environment: dict[str, str]
    packages: tuple[str, ...]
    desktop: DesktopPlan | None = None


class RuntimePort(Protocol):
    def lock(self) -> str | None: ...
    def inputs(self) -> list[tuple[Path, bool]]: ...
    def prepare(self) -> Path: ...
    def existing(self) -> Path: ...


class NetworkPort(Protocol):
    def start(self, subscription: str | None) -> NetworkReady: ...
    def close(self) -> None: ...


class PackagesPort(Protocol):
    def install(self) -> None: ...
    def activate_happ(self) -> None: ...


class NetworkFactory(Protocol):
    def __call__(
        self,
        root: Path,
        target: system.TargetUser,
        environment: dict[str, str],
        *,
        subscription_file: Path | None = None,
    ) -> NetworkPort: ...


class PackageFactory(Protocol):
    def __call__(
        self,
        root: Path,
        *,
        target: system.TargetUser,
        happ: Path,
        proxy: str | None,
        paths: system.SystemPaths,
        run: Callable[..., subprocess.CompletedProcess[str]],
        emit: Callable[[str], None],
        extra_packages: tuple[str, ...] = (),
    ) -> AbstractContextManager[PackagesPort]: ...


class DesktopPort(Protocol):
    def plan(
        self,
        target: system.TargetUser,
        environment: Mapping[str, str],
        *,
        timezone: str | None = None,
    ) -> DesktopPlan | None: ...
    def revalidate(self, plan: DesktopPlan) -> DesktopPlan: ...
    def apply(self, plan: DesktopPlan) -> None: ...


class UnsupportedTargetError(ValueError):
    """The selected machine cannot execute this installation profile."""


@dataclass
class ApplyProgress:
    stage: InstallStage = InstallStage.TEMPORARY_NETWORK
    user_prepared: bool = False
    user_configured: bool = False
    desktop_configured: bool = False

    def completed(self) -> tuple[str, ...]:
        return tuple(
            stage
            for stage, done in (
                ("user-downloads", self.user_prepared),
                ("user-configuration", self.user_configured),
                ("system-desktop", self.desktop_configured),
            )
            if done
        )


class InstallationService:
    """Own preflight and download/cleanup/offline-apply ordering behind explicit ports."""

    def __init__(
        self,
        root: Path,
        *,
        runtime: RuntimePort | None = None,
        network: NetworkFactory = TemporaryNetwork,
        packages: PackageFactory | None = None,
        paths: system.SystemPaths | None = None,
        interaction: Interaction | None = None,
        desktop: DesktopPort | None = None,
    ) -> None:
        self.interaction = ConsoleInteraction() if interaction is None else interaction
        self.root = root.resolve()
        self.runtime = UserRuntime(self.root) if runtime is None else runtime
        self.network = network
        self.packages: PackageFactory = system.prepare_packages if packages is None else packages
        self.paths = system.SystemPaths() if paths is None else paths
        self.desktop: DesktopPort = (
            DesktopSystem(self.root, run=system.run_command) if desktop is None else desktop
        )

    def plan(self, request: InstallRequest) -> InstallPlan:
        timezone = validate_timezone(request.timezone)
        try:
            packages = tuple(system.current_packages(self.root))
        except (OSError, ValueError) as exc:
            raise UnsupportedTargetError(str(exc)) from exc
        target = system.resolve_target(request.user)
        environment = system.user_environment(target, request.overrides)
        system.validate_installation_paths(self.root, target, environment)
        for path in (
            "uv.lock",
            "pyproject.toml",
            "manifests/platforms.toml",
            "manifests/runtime.toml",
            "manifests/network.toml",
            "manifests/happ.toml",
            "assets/hyprland/profile.lua",
            "src/setup_system/_worker_entry.py",
            "src/setup_system/worker_protocol.py",
            "src/setup_system/application/interaction.py",
            "src/setup_system/runtime.py",
            "src/setup_system/capabilities.py",
            "src/setup_system/downloads.py",
            "src/setup_system/font_policy.py",
            "src/setup_system/installation_state.py",
            "src/setup_system/system_inspection.py",
            "src/setup_system/desktop_system.py",
            "assets/login/login-session.py",
            "assets/login/greeter.css",
            "assets/hyprland/panel.py",
            "src/setup_system/adapters/debian/package_session.py",
            "src/setup_system/application/installer.py",
            "src/setup_system/network_worker.py",
        ):
            if not (self.root / path).is_file():
                raise ValueError("Incomplete local source bundle")
        self.runtime.lock()
        system.validate_source_access(
            self.root, target, self.runtime.inputs(), run=system.run_command
        )
        if request.subscription_file is not None:
            from setup_system.network_worker import validate_subscription_file

            if not request.subscription_file.is_absolute():
                raise ValueError("--vpn-config-file requires an absolute private filename")
            try:
                validate_subscription_file(request.subscription_file, target)
            except (OSError, ValueError):
                raise ValueError(
                    "VPN input file is missing, unsafe or not privately owned"
                ) from None
        if request.operation in {Operation.PLAN, Operation.APPLY}:
            try:
                system.validate_service_manager(root=self.root, run=system.run_command)
            except ValueError as exc:
                raise UnsupportedTargetError(str(exc)) from exc
        desktop = (
            self.desktop.plan(target, environment, timezone=timezone)
            if request.operation in {Operation.PLAN, Operation.APPLY}
            else None
        )
        if desktop is not None:
            packages = tuple(dict.fromkeys((*packages, *desktop.extra_packages)))
        return InstallPlan(target, environment, packages, desktop)

    def show_plan(self, plan: InstallPlan) -> None:
        target = plan.target
        self.interaction.write(
            f"Hyprland plan for {target.name} (uid={target.uid}, gid={target.gid}), "
            f"HOME={target.home}"
        )
        self.interaction.write(
            "1. Non-root temporary pinned Xray: hidden HTTPS subscription input; "
            "loopback HTTP only."
        )
        self.interaction.write(
            "2. Download and verify Happ, signed Debian/backports packages, "
            "locked runtime and fonts."
        )
        self.interaction.write(
            "3. Stop/remove the temporary client and its configuration before applying packages."
        )
        self.interaction.write(
            "4. Offline package apply; defer vendor restarts, then start the required Happ helper."
        )
        self.interaction.write(
            "5. Offline Typer user configuration with backups; "
            "no automatic graphical session/reboot."
        )
        self.interaction.write("Packages: python3 ca-certificates happ " + " ".join(plan.packages))
        self.interaction.write(
            f"User runtime: uv {UV_VERSION}, Python {RUNTIME_PYTHON}; "
            + plan.environment["XDG_CONFIG_HOME"]
        )
        self.interaction.write(
            "Preserve network configuration, locale, login shell and session; "
            "change timezone only when explicitly selected."
        )
        if plan.desktop is not None:
            for detail in plan.desktop.describe():
                self.interaction.write(detail)
        self.interaction.write(
            "Plan performs no downloads or metadata refresh. "
            "Initial client/subscription access is required."
        )

    def _output(self, result: subprocess.CompletedProcess[str]) -> None:
        if result.stdout:
            self.interaction.write(result.stdout, end="")
        if result.stderr:
            self.interaction.write(result.stderr, end="", error=True)

    def _user_action(self, plan: InstallPlan, *, prepare: bool) -> int:
        os.environ.update(plan.environment)
        runtime = self.runtime.prepare() if prepare else self.runtime.existing()
        arguments = [str(runtime / "bin/setup-system"), "workstation"]
        arguments += (
            ["prefetch", "--profile", "hyprland"]
            if prepare
            else ["apply", "--profile", "hyprland", "--offline", "--yes"]
        )
        result = system.run_command(arguments, cwd=plan.target.home, timeout=900, check=False)
        self._output(result)
        if result.returncode:
            self.interaction.write(f"FAILED user command: exit {result.returncode}", error=True)
            return 5
        return 0

    def _user_process(
        self, plan: InstallPlan, operation: Operation, *, proxy: str | None = None
    ) -> int:
        from setup_system.worker_protocol import worker_arguments

        validate_home_access(plan.target.home)
        environment = dict(plan.environment)
        if proxy is not None:
            environment.update(HTTP_PROXY=proxy, HTTPS_PROXY=proxy)
        offline = operation == Operation.USER_STAGE
        if offline:
            environment["UV_OFFLINE"] = "true"
        arguments = [
            sys.executable,
            "-I",
            "-B",
            str(self.root / "src/setup_system/_worker_entry.py"),
            *worker_arguments(operation, plan.target.name, environment),
        ]
        if offline:
            # Create only this process's network namespace, then restore the real user's groups.
            arguments = [
                "/usr/bin/unshare",
                "--net",
                "--",
                "/usr/bin/setpriv",
                "--reuid",
                str(plan.target.uid),
                "--regid",
                str(plan.target.gid),
                "--init-groups",
                *arguments,
            ]
            result = system.run_command(
                arguments,
                cwd=plan.target.home,
                env=environment,
                umask=0o077,
                timeout=1200,
                termination_grace=15,
                check=False,
            )
        else:
            result = system.run_command(
                arguments,
                cwd=plan.target.home,
                env=environment,
                user=plan.target.uid,
                group=plan.target.gid,
                extra_groups=os.getgrouplist(plan.target.name, plan.target.gid),
                umask=0o077,
                timeout=2400,
                termination_grace=15,
                check=False,
            )
        self._output(result)
        return result.returncode if result.returncode >= 0 else 5

    def run(self, request: InstallRequest) -> int:
        private = request.operation in {
            Operation.USER_STAGE,
            Operation.PREPARE_USER,
            Operation.NETWORK_STAGE,
        }
        if private and os.geteuid() == 0:
            self.interaction.write("Refusing root user-stage.", error=True)
            return 2
        if request.operation == Operation.PLAN and request.yes:
            self.interaction.write("--yes belongs to apply.", error=True)
            return 2
        try:
            plan = self.plan(request)
        except UnsupportedTargetError as exc:
            self.interaction.write(f"UNSUPPORTED: {exc}. No changes made.", error=True)
            return 4
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.interaction.write(f"Preflight failed: {error_text(exc)}", error=True)
            return 2
        if private:
            return self._run_private(plan, request)
        self.show_plan(plan)
        if request.operation == Operation.PLAN:
            return 0
        if not self._consent(request):
            return 2
        return self._apply(plan, request)

    def _run_private(self, plan: InstallPlan, request: InstallRequest) -> int:
        try:
            validate_home_access(plan.target.home)
            if request.operation == Operation.NETWORK_STAGE:
                from setup_system.network_worker import serve_network

                os.environ.update(plan.environment)
                return serve_network(self.root, plan.target, request.subscription_file)
            return self._user_action(plan, prepare=request.operation == Operation.PREPARE_USER)
        except (
            OSError,
            ValueError,
            RuntimeError,
            subprocess.SubprocessError,
            KeyboardInterrupt,
        ) as exc:
            self.interaction.write(
                f"FAILED user runtime/configuration: {type(exc).__name__}: {error_text(exc)}",
                error=True,
            )
            return 5

    def _consent(self, request: InstallRequest) -> bool:
        if os.geteuid() != 0:
            self.interaction.write(
                "Run setup-system install apply with root privileges and a verified target user.",
                error=True,
            )
            return False
        if not request.yes:
            if not self.interaction.interactive:
                self.interaction.write("No TTY: apply requires explicit --yes consent.", error=True)
                return False
            if not self.interaction.confirm("Apply the complete plan?"):
                return False
        if request.subscription_file is None and not self.interaction.interactive:
            self.interaction.write(
                "No TTY: temporary VPN requires a private --vpn-config-file.", error=True
            )
            return False
        return True

    def _apply(self, plan: InstallPlan, request: InstallRequest) -> int:
        try:
            from setup_system.network_worker import subscription_address

            subscription = (
                None
                if request.subscription_file is not None
                else subscription_address(self.interaction.subscription())
            )
        except (EOFError, OSError, ValueError):
            self.interaction.write(
                "A valid hidden HTTPS subscription or private input file is required.",
                error=True,
            )
            return 2
        progress = ApplyProgress()
        try:
            validate_home_access(plan.target.home)
            network = self.network(
                self.root,
                plan.target,
                plan.environment,
                subscription_file=request.subscription_file,
            )
            self.interaction.write(
                "Stage: temporary download network and verified Happ artifact", flush=True
            )
            try:
                ready = network.start(subscription)
                subscription = None
                self._apply_packages(plan, network, ready, progress)
            finally:
                network.close()
            system.mark_user_stage(self.paths, "complete", completed=progress.completed())
        except (
            OSError,
            ValueError,
            RuntimeError,
            subprocess.SubprocessError,
            KeyboardInterrupt,
        ) as exc:
            return self._failed(progress, exc)
        return self._complete(plan)

    def _apply_packages(
        self,
        plan: InstallPlan,
        network: NetworkPort,
        ready: NetworkReady,
        progress: ApplyProgress,
    ) -> None:
        progress.stage = InstallStage.PACKAGE_DOWNLOADS
        extra = plan.desktop.extra_packages if plan.desktop is not None else ()
        with self.packages(
            self.root,
            target=plan.target,
            happ=ready.happ,
            proxy=ready.http_proxy,
            paths=self.paths,
            run=system.run_command,
            emit=self.interaction.write,
            extra_packages=extra,
        ) as packages:
            progress.stage = InstallStage.USER_DOWNLOADS
            self.interaction.write("Stage: locked user runtime and font downloads", flush=True)
            code = self._user_process(plan, Operation.PREPARE_USER, proxy=ready.http_proxy)
            if code:
                raise RuntimeError(f"User downloads failed with exit {code}")
            progress.user_prepared = True
            progress.stage = InstallStage.TEMPORARY_NETWORK_CLEANUP
            network.close()
            self.interaction.write(
                "Temporary download client/config removed; "
                "package and user-file apply use offline namespaces.",
                flush=True,
            )
            progress.stage = InstallStage.PACKAGES
            packages.install()
            progress.stage = InstallStage.HAPP_SERVICE
            packages.activate_happ()
            progress.stage = InstallStage.USER
            self.interaction.write("Stage: offline user configuration through Typer", flush=True)
            code = self._user_process(plan, Operation.USER_STAGE)
            if code:
                raise RuntimeError(f"User configuration failed with exit {code}")
            progress.user_configured = True
            if plan.desktop is not None:
                progress.stage = InstallStage.SYSTEM_DESKTOP
                system.mark_user_stage(
                    self.paths,
                    "running",
                    stage=progress.stage,
                    completed=progress.completed(),
                )
                self.interaction.write(
                    "Stage: graphical login, existing encryption, clock and GRUB", flush=True
                )
                desktop_plan = self.desktop.revalidate(plan.desktop)
                self.desktop.apply(desktop_plan)
                progress.desktop_configured = True

    def _failed(self, progress: ApplyProgress, exc: BaseException) -> int:
        paths = self.paths
        if paths.journal.is_file() and not paths.journal.is_symlink():
            try:
                system.mark_user_stage(
                    paths,
                    "failed",
                    stage=None
                    if progress.stage
                    in {
                        InstallStage.PACKAGE_DOWNLOADS,
                        InstallStage.PACKAGES,
                        InstallStage.HAPP_SERVICE,
                    }
                    else progress.stage,
                    completed=progress.completed(),
                )
            except (OSError, ValueError) as journal_error:
                self.interaction.write(
                    f"Journal update failed: {error_text(journal_error)}", error=True
                )
            journal = f"journal: {paths.journal}"
        else:
            journal = "journal not created (failure before the first record)"
        self.interaction.write(
            f"FAILED installation stage {progress.stage}: {error_text(exc)}; {journal}. "
            "No APT rollback is promised.",
            error=True,
        )
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            self.interaction.write(error_text(exc.stderr), error=True)
        return 5

    def _complete(self, plan: InstallPlan) -> int:
        launcher = Path(plan.environment["XDG_DATA_HOME"]) / "setup-system/bin/start-hyprland"
        recovery = launcher.parent.parent / "runtime/bin/setup-system"
        self.interaction.write(
            "Installation completed; REAL LOGIN/GUI must be checked on the target machine."
        )
        if plan.desktop is not None:
            self.interaction.write(
                "Next normal boot: authenticate on the graphical login screen as "
                + plan.target.name
                + "; Hyprland starts after authentication. No reboot performed."
            )
            self.interaction.write(
                "GRUB menu delay is zero; existing encrypted-disk unlock prompts remain. "
                "TTY recovery: Ctrl+Alt+F3."
            )
        self.interaction.write(
            "TTY fallback: log in locally as "
            + plan.target.name
            + " and run: "
            + shlex.quote(str(launcher))
        )
        self.interaction.write(
            "Open Happ from the launcher and use its native subscription import; "
            "no secret link was saved."
        )
        self.interaction.write("Log out: Super+F9 then Logout; switch to TTY with Ctrl+Alt+F3.")
        self.interaction.write(
            "Recovery preview: " + shlex.quote(str(recovery)) + " workstation restore BACKUP_ID"
        )
        return 0
