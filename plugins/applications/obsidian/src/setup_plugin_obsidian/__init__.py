"""Manage a selected vault through Obsidian's supported filesystem and CLI interfaces."""

import asyncio
import os
from pathlib import Path
from typing import ClassVar, Literal
from urllib.parse import quote

from pydantic import Field

from setup_system.infrastructure.processes import run_command
from setup_system.infrastructure.system_paths import protected
from setup_system.infrastructure.user_storage import (
    checked_directory,
    inside_home,
    private_directory,
)
from setup_system_sdk import Inputs, Outcome, Plan, PluginSettings, Resource, Result
from setup_system_sdk.apt import DebianPackages, apply_packages
from setup_system_sdk.native import command


class Settings(PluginSettings):
    vault: Path | None = Field(
        default=None, title="Selected vault", json_schema_extra={"ui": {"widget": "path"}}
    )


class NewVault(Inputs):
    path: Path = Field(title="New vault directory", json_schema_extra={"ui": {"widget": "path"}})


LAUNCHER = Path("/usr/share/applications/obsidian.desktop")


class OpenVault(Inputs):
    """Ask the official native application to open the selected vault."""


class Hotkey(Inputs):
    command_id: str = Field(
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:/-]{0,127}$",
        title="Command",
        json_schema_extra={"ui": {"choice_provider": "commands"}},
    )


class Theme(Inputs):
    name: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[\w .()-]+$",
        title="Installed theme",
        json_schema_extra={"ui": {"choice_provider": "themes"}},
    )


class CoreFeature(Inputs):
    feature: Literal["bookmarks", "outline", "daily-notes", "templates"] = "bookmarks"
    enabled: bool = True


def vault(path: Path) -> Path:
    result = checked_directory(inside_home(path))
    if result == Path.home() or any(
        part.startswith(".") for part in result.relative_to(Path.home()).parts
    ):
        raise ValueError("Select a dedicated visible vault folder inside HOME")
    if any((directory / ".obsidian").is_dir() for directory in result.parents):
        raise ValueError("Nested vaults are unsupported")
    return result


def native(selected: Path, *args: str) -> str:
    status, data = command("/usr/bin/obsidian", "vault=" + selected.name, *args, capture=True)
    if status or data.strip().startswith(("Error:", "Unknown command")):
        raise ValueError("The running Obsidian CLI did not confirm this request")
    return data.strip()


def running(selected: Path) -> bool:
    status, _ = command("/usr/bin/pgrep", "-u", str(os.getuid()), "-ix", "obsidian")
    return status == 0 and native(selected, "vault", "info=path") == str(selected)


class Plugin(DebianPackages):
    executable = "/usr/bin/obsidian"
    settings_model = Settings
    package_folder = Path(__file__).parent
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        "check": Inputs,
        "create-vault": NewVault,
        "open": OpenVault,
        "set-theme": Theme,
        "core-feature": CoreFeature,
        "hotkeys": Inputs,
        "read-hotkey": Hotkey,
        "editor": Inputs,
        "account-sync": Inputs,
        "community-plugins": Inputs,
    }
    manual: ClassVar[dict[str, str]] = {
        "account-sync": (
            "In Obsidian open Settings → General to sign in, then enable Sync and select its "
            "remote vault."
        ),
        "community-plugins": (
            "In Obsidian open Settings → Community plugins. Review each extension and its "
            "source before enabling it."
        ),
        "hotkeys": (
            "In the selected vault open Settings → Hotkeys. Search for the command and choose "
            "its shortcut."
        ),
        "editor": (
            "In the selected vault open Settings → Editor. Choose the editing behavior and "
            "check it within the same vault."
        ),
    }

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if isinstance(inputs, NewVault):
            path = vault(inputs.path)
            if not path.parent.is_dir() or path.exists():
                return Plan(
                    outcome=Outcome.BLOCKED,
                    summary="Choose a new vault name under an existing parent",
                )
            parent = path.parent.stat()
            return Plan(
                summary=f"Create an empty user-owned vault folder at {path}",
                resources=[
                    Resource(
                        kind="vault",
                        identity=str(path),
                        ownership="created",
                        after=f"{parent.st_dev}:{parent.st_ino}",
                        cleanup=False,
                    )
                ],
                warnings=["Notes and attachments in this folder are excluded from cleanup."],
            )
        if action_id in {"open", "set-theme", "core-feature", "read-hotkey"}:
            if not isinstance(settings, Settings) or not settings.vault:
                return Plan(outcome=Outcome.BLOCKED, summary="Select and save a vault path")
            selected = vault(settings.vault)
            if not selected.is_dir():
                return Plan(outcome=Outcome.BLOCKED, summary="Selected vault folder is absent")
            if action_id == "open":
                protected(LAUNCHER, directory=False)
                if not LAUNCHER.is_file():
                    return Plan(
                        outcome=Outcome.BLOCKED,
                        summary="Install the official Obsidian desktop launcher first",
                    )
                return Plan(
                    summary=f"Open vault {selected} in the official application",
                    warnings=["The native vault manager may ask you to register this folder."],
                )
            if not running(selected):
                return Plan(
                    outcome=Outcome.NEEDS_USER_ACTION,
                    summary=(
                        "Open this vault and enable Command line interface in Obsidian "
                        "Settings → General"
                    ),
                )
            if isinstance(inputs, Hotkey):
                if inputs.command_id not in native(selected, "commands").splitlines():
                    return Plan(
                        outcome=Outcome.BLOCKED, summary="The command is no longer available"
                    )
                value = native(selected, "hotkey", "id=" + inputs.command_id)
                if len(value) > 300 or not value.isprintable():
                    return Plan(outcome=Outcome.BLOCKED, summary="Unexpected shortcut output")
                return Plan(
                    outcome=Outcome.NO_CHANGES,
                    summary=f"{inputs.command_id} — {value or 'Unassigned'}",
                )
            if isinstance(inputs, Theme):
                if inputs.name not in native(selected, "themes").splitlines():
                    return Plan(
                        outcome=Outcome.BLOCKED, summary="Theme is absent from the selected vault"
                    )
                before = native(selected, "theme")
                after = inputs.name
                key = "theme"
            elif isinstance(inputs, CoreFeature):
                features = native(selected, "plugins", "filter=core").splitlines()
                if inputs.feature not in features:
                    return Plan(
                        outcome=Outcome.BLOCKED, summary="Core feature unavailable in this release"
                    )
                before = str(
                    inputs.feature
                    in native(selected, "plugins:enabled", "filter=core").splitlines()
                )
                after = str(inputs.enabled)
                key = "core/" + inputs.feature
            else:
                raise ValueError("Unexpected vault request")
            return Plan(
                outcome=Outcome.NO_CHANGES if before == after else Outcome.READY,
                summary=f"Configure {key} in {selected}",
                resources=[
                    Resource(
                        kind="vault-setting",
                        identity=f"{selected}/{key}",
                        ownership="modified",
                        before=before,
                        after=after,
                    )
                ],
            )
        return super().prepare(action_id, settings, inputs)

    async def choices(self, provider: str, query: str, settings: PluginSettings) -> list[str]:
        if (
            provider not in {"themes", "commands"}
            or not isinstance(settings, Settings)
            or not settings.vault
        ):
            return []
        selected = vault(settings.vault)
        if not await asyncio.to_thread(running, selected):
            return []
        values = await asyncio.to_thread(native, selected, provider)
        return [v for v in values.splitlines() if query.casefold() in v.casefold()][:50]

    def mutate(
        self, action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan
    ) -> Result:
        if isinstance(inputs, NewVault):
            path = vault(inputs.path)
            with private_directory(path.parent, create=False) as parent:
                info = os.fstat(parent)
                if plan.resources[0].after != f"{info.st_dev}:{info.st_ino}":
                    return Result(outcome=Outcome.BLOCKED, message="Vault parent changed")
                os.mkdir(path.name, 0o700, dir_fd=parent)
                os.fsync(parent)
        elif isinstance(inputs, OpenVault) and isinstance(settings, Settings) and settings.vault:
            selected = vault(settings.vault)
            environment = {
                k: os.environ[k]
                for k in (
                    "DISPLAY",
                    "WAYLAND_DISPLAY",
                    "XDG_RUNTIME_DIR",
                    "DBUS_SESSION_BUS_ADDRESS",
                    "XAUTHORITY",
                    "XDG_CONFIG_HOME",
                    "XDG_DATA_HOME",
                )
                if k in os.environ
            }
            rc = run_command(
                [
                    "/usr/bin/gio",
                    "launch",
                    str(LAUNCHER),
                    "obsidian://open?path=" + quote(str(selected), safe=""),
                ],
                env={**environment, "HOME": str(Path.home()), "PATH": "/usr/bin:/bin"},
                cwd="/",
                discard_output=True,
                check=False,
                timeout=10,
            )
            if rc.returncode:
                return Result(
                    outcome=Outcome.UNKNOWN_OUTCOME, message="Inspect the native vault selector"
                )
        elif (
            isinstance(settings, Settings)
            and settings.vault
            and isinstance(inputs, (Theme, CoreFeature))
        ):
            selected = vault(settings.vault)
            if not running(selected):
                return Result(outcome=Outcome.BLOCKED, message="Selected vault is no longer active")
            if isinstance(inputs, Theme):
                native(selected, "theme:set", "name=" + inputs.name)
            else:
                native(
                    selected,
                    "plugin:enable" if inputs.enabled else "plugin:disable",
                    "id=" + inputs.feature,
                )
        else:
            raise ValueError("Unexpected vault action")
        return Result(
            outcome=Outcome.CHANGED, message="Vault action submitted", resources=plan.resources
        )

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        if isinstance(inputs, OpenVault) and isinstance(settings, Settings) and settings.vault:
            try:
                confirmed = await asyncio.to_thread(running, vault(settings.vault))
            except (ValueError, OSError):
                confirmed = False
            return Result(
                outcome=Outcome.COMPLETE if confirmed else Outcome.NEEDS_USER_ACTION,
                message="Vault opened"
                if confirmed
                else "Check the selected native vault and enable CLI readback in Settings",
            )
        if isinstance(inputs, NewVault):
            ok = await asyncio.to_thread(lambda: vault(inputs.path).is_dir())
        elif isinstance(inputs, (Theme, CoreFeature)):
            result = await self.plan(
                "set-theme" if isinstance(inputs, Theme) else "core-feature", settings, inputs
            )
            ok = result.outcome == Outcome.NO_CHANGES
        else:
            return await super().verify(plan, settings, inputs)
        return Result(
            outcome=Outcome.COMPLETE if ok else Outcome.PARTIAL,
            message="Selected vault state verified" if ok else "Vault state differs",
            resources=plan.resources,
        )


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    apply_packages("obsidian", action_id, plan)
