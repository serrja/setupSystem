"""One installation use case shared by Typer and the dependency-free source handoff."""

from __future__ import annotations

import getpass
import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from setup_system import installation as system
from setup_system.diagnostics import error_text
from setup_system.network_session import NetworkReady, TemporaryNetwork
from setup_system.runtime import RUNTIME_PYTHON, UV_VERSION, UserRuntime


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


@dataclass(frozen=True)
class InstallPlan:
    target: system.TargetUser
    environment: dict[str, str]
    packages: tuple[str, ...]


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


class UnsupportedTargetError(ValueError):
    """The selected machine cannot execute this installation profile."""


class InstallationService:
    """Own preflight and download/cleanup/offline-apply ordering behind explicit ports."""

    def __init__(
        self,
        root: Path,
        *,
        runtime: RuntimePort | None = None,
        network: Callable[..., NetworkPort] = TemporaryNetwork,
        packages: Callable[..., AbstractContextManager[PackagesPort]] | None = None,
        paths: system.SystemPaths | None = None,
    ) -> None:
        self.root = root.resolve()
        self.runtime = UserRuntime(self.root) if runtime is None else runtime
        self.network = network
        self.packages = system.prepare_packages if packages is None else packages
        self.paths = system.SystemPaths() if paths is None else paths

    def plan(self, request: InstallRequest) -> InstallPlan:
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
            "manifests/network.toml",
            "manifests/happ.toml",
            "assets/hyprland/profile.lua",
            "src/setup_system/_installer_entry.py",
            "src/setup_system/runtime.py",
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
        return InstallPlan(target, environment, packages)

    @staticmethod
    def show_plan(plan: InstallPlan) -> None:
        target = plan.target
        print(
            f"Hyprland plan for {target.name} (uid={target.uid}, gid={target.gid}), "
            f"HOME={target.home}"
        )
        print(
            "1. Non-root temporary pinned Xray: hidden HTTPS subscription input; "
            "loopback HTTP only."
        )
        print(
            "2. Download and verify Happ, signed Debian/backports packages, "
            "locked runtime and fonts."
        )
        print("3. Stop/remove the temporary client and its configuration before applying packages.")
        print(
            "4. Offline package apply; defer vendor restarts, then start the required Happ helper."
        )
        print(
            "5. Offline Typer user configuration with backups; "
            "no automatic graphical session/reboot."
        )
        print("Packages: python3 ca-certificates happ " + " ".join(plan.packages))
        print(
            f"User runtime: uv {UV_VERSION}, Python {RUNTIME_PYTHON}; "
            + plan.environment["XDG_CONFIG_HOME"]
        )
        print("Preserve existing network configuration, locale, timezone, login shell and session.")
        print(
            "Plan performs no downloads or metadata refresh. "
            "Initial client/subscription access is required."
        )

    @staticmethod
    def _output(result: subprocess.CompletedProcess[str]) -> None:
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)

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
            print(f"FAILED user command: exit {result.returncode}", file=sys.stderr)
            return 5
        return 0

    def _user_process(
        self, plan: InstallPlan, operation: Operation, *, proxy: str | None = None
    ) -> int:
        from setup_system.entrypoint import source_arguments

        environment = dict(plan.environment)
        if proxy is not None:
            environment.update(HTTP_PROXY=proxy, HTTPS_PROXY=proxy)
        offline = operation == Operation.USER_STAGE
        if offline:
            environment["UV_OFFLINE"] = "true"
        arguments = [
            "/usr/bin/python3",
            "-I",
            "-B",
            str(self.root / "src/setup_system/_installer_entry.py"),
            *source_arguments(operation, plan.target.name, environment, yes=True),
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
            print("Refusing root user-stage.", file=sys.stderr)
            return 2
        if request.operation == Operation.PLAN and request.yes:
            print("--yes belongs to apply.", file=sys.stderr)
            return 2
        try:
            plan = self.plan(request)
        except UnsupportedTargetError as exc:
            print(f"UNSUPPORTED: {exc}. No changes made.", file=sys.stderr)
            return 4
        except (OSError, ValueError) as exc:
            print(f"Preflight failed: {error_text(exc)}", file=sys.stderr)
            return 2
        if private:
            try:
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
                print(
                    f"FAILED user runtime/configuration: {type(exc).__name__}: {error_text(exc)}",
                    file=sys.stderr,
                )
                return 5
        self.show_plan(plan)
        if request.operation == Operation.PLAN:
            return 0
        if os.geteuid() != 0:
            print("Use sudo bash ./setup.sh for installation.", file=sys.stderr)
            return 2
        if not request.yes:
            if not sys.stdin.isatty():
                print("No TTY: apply requires explicit --yes consent.", file=sys.stderr)
                return 2
            if input("Apply the complete plan? [y/N] ").strip().lower() not in {"y", "yes"}:
                return 2
        if request.subscription_file is None and not sys.stdin.isatty():
            print("No TTY: temporary VPN requires a private --vpn-config-file.", file=sys.stderr)
            return 2
        try:
            from setup_system.network_worker import subscription_address

            subscription = (
                None
                if request.subscription_file is not None
                else subscription_address(getpass.getpass("HTTPS VPN subscription (hidden): "))
            )
        except (EOFError, OSError, ValueError):
            print(
                "A valid hidden HTTPS subscription or private input file is required.",
                file=sys.stderr,
            )
            return 2
        paths = self.paths
        network = self.network(
            self.root, plan.target, plan.environment, subscription_file=request.subscription_file
        )
        stage = "temporary-network"
        user_prepared = False
        try:
            print("Stage: temporary download network and verified Happ artifact", flush=True)
            try:
                ready = network.start(subscription)
                subscription = None
                stage = "package-downloads"
                with self.packages(
                    self.root,
                    target=plan.target,
                    happ=ready.happ,
                    proxy=ready.http_proxy,
                    paths=paths,
                    run=system.run_command,
                ) as packages:
                    stage = "user-downloads"
                    print("Stage: locked user runtime and font downloads", flush=True)
                    code = self._user_process(plan, Operation.PREPARE_USER, proxy=ready.http_proxy)
                    if code:
                        raise RuntimeError(f"User downloads failed with exit {code}")
                    user_prepared = True
                    stage = "temporary-network-cleanup"
                    network.close()
                    print(
                        "Temporary download client/config removed; "
                        "package and user-file apply use offline namespaces.",
                        flush=True,
                    )
                    stage = "packages"
                    packages.install()
                    stage = "happ-service"
                    packages.activate_happ()
                    stage = "user"
                    print("Stage: offline user configuration through Typer", flush=True)
                    code = self._user_process(plan, Operation.USER_STAGE)
                    if code:
                        raise RuntimeError(f"User configuration failed with exit {code}")
            finally:
                network.close()
            system.mark_user_stage(
                paths, "complete", completed=("user-downloads", "user-configuration")
            )
        except (
            OSError,
            ValueError,
            RuntimeError,
            subprocess.SubprocessError,
            KeyboardInterrupt,
        ) as exc:
            if paths.journal.is_file() and not paths.journal.is_symlink():
                try:
                    system.mark_user_stage(
                        paths,
                        "failed",
                        stage=None
                        if stage in {"package-downloads", "packages", "happ-service"}
                        else stage,
                        completed=("user-downloads",) if user_prepared else (),
                    )
                except (OSError, ValueError) as journal_error:
                    print(f"Journal update failed: {error_text(journal_error)}", file=sys.stderr)
                journal = f"journal: {paths.journal}"
            else:
                journal = "journal not created (failure before the first record)"
            print(
                f"FAILED installation stage {stage}: {error_text(exc)}; {journal}. "
                "No APT rollback is promised.",
                file=sys.stderr,
            )
            if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
                print(error_text(exc.stderr), file=sys.stderr)
            return 5
        launcher = Path(plan.environment["XDG_DATA_HOME"]) / "setup-system/bin/start-hyprland"
        recovery = launcher.parent.parent / "runtime/bin/setup-system"
        print("Installation completed; REAL LOGIN/GUI must be checked on the target machine.")
        print(
            "Next: log in locally on a TTY as "
            + plan.target.name
            + " and run: "
            + shlex.quote(str(launcher))
        )
        print(
            "Open Happ from the launcher and use its native subscription import; "
            "no secret link was saved."
        )
        print("Return to TTY: Super+F9 then Logout; switch consoles with Ctrl+Alt+F3.")
        print("Recovery preview: " + shlex.quote(str(recovery)) + " workstation restore BACKUP_ID")
        return 0
