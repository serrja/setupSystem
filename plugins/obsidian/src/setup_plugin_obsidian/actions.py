"""Manage a selected vault through Obsidian's supported filesystem and CLI interfaces."""

import os
from enum import StrEnum
from importlib.resources import files as package_files
from pathlib import Path
from typing import ClassVar
from urllib.parse import quote

from setup_system_sdk.blocking import run_sync
from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.resources import ResourceOwnership
from setup_system_sdk.files.backend import NativeBackend, command
from setup_system_sdk.files.user_storage import (
    checked_directory,
    inside_home,
    private_directory,
)
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.processes import run_command
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan, Resource, Result

from setup_plugin_obsidian.adapters.package import LAUNCHER, launcher_available, registered_cli
from setup_plugin_obsidian.enums import PluginAction
from setup_plugin_obsidian.models import (
    CoreFeature,
    EditorSnippet,
    Hotkey,
    NewVault,
    OpenVault,
    Settings,
    Theme,
)


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
    cli = registered_cli()
    if cli is None:
        raise ValueError("Enable the current Debian installer's CLI in Obsidian Settings → General")
    status, data = command(str(cli), "vault=" + selected.name, *args, capture=True)
    if status or data.strip().startswith(("Error:", "Unknown command")):
        raise ValueError("The running Obsidian CLI did not confirm this request")
    return data.strip()


def running(selected: Path) -> bool:
    status, _ = command("/usr/bin/pgrep", "-u", str(os.getuid()), "-ix", "obsidian")
    if status != 0:
        return False
    try:
        return native(selected, "vault", "info=path") == str(selected)
    except (OSError, ValueError):
        return False


class PluginActions(NativeBackend):
    executable = "/usr/bin/obsidian"

    package_folder = Path(str(package_files("setup_plugin_obsidian").joinpath("resources")))

    manual: ClassVar[dict[StrEnum, str]] = {
        PluginAction.ACCOUNT_SYNC: (
            "In Obsidian open Settings → General to sign in, then enable Sync and select its "
            "remote vault."
        ),
        PluginAction.COMMUNITY_PLUGINS: (
            "In Obsidian open Settings → Community plugins. Review each extension and its "
            "source before enabling it."
        ),
        PluginAction.HOTKEYS: (
            "In the selected vault open Settings → Hotkeys. Search for the command and choose "
            "its shortcut."
        ),
        PluginAction.EDITOR: (
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
                        ownership=ResourceOwnership.CREATED,
                        after=f"{parent.st_dev}:{parent.st_ino}",
                        cleanup=False,
                    )
                ],
                warnings=["Notes and attachments in this folder are excluded from cleanup."],
            )
        if action_id in {
            PluginAction.OPEN,
            PluginAction.SET_THEME,
            PluginAction.CORE_FEATURE,
            PluginAction.EDITOR_SNIPPET,
            PluginAction.READ_HOTKEY,
        }:
            if not isinstance(settings, Settings) or not settings.vault:
                return Plan(outcome=Outcome.BLOCKED, summary="Select and save a vault path")
            selected = vault(settings.vault)
            if not selected.is_dir():
                return Plan(outcome=Outcome.BLOCKED, summary="Selected vault folder is absent")
            if action_id == PluginAction.OPEN:
                if not launcher_available():
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
            elif isinstance(inputs, EditorSnippet):
                if inputs.name not in native(selected, "snippets").splitlines():
                    return Plan(
                        outcome=Outcome.BLOCKED,
                        summary=(
                            "The CSS snippet is absent from this vault. "
                            "Review Appearance → CSS snippets."
                        ),
                    )
                before = str(inputs.name in native(selected, "snippets:enabled").splitlines())
                after = str(inputs.enabled)
                key = "snippet/" + inputs.name
            else:
                raise ValueError("Unexpected vault request")
            return Plan(
                outcome=Outcome.NO_CHANGES if before == after else Outcome.READY,
                summary=f"Configure {key} in {selected}",
                warnings=["Enabling this CSS applies the selected snippet to this vault."]
                if isinstance(inputs, EditorSnippet) and inputs.enabled and before != after
                else [],
                resources=[
                    Resource(
                        kind="vault-setting",
                        identity=f"{selected}/{key}",
                        ownership=ResourceOwnership.MODIFIED,
                        before=before,
                        after=after,
                    )
                ],
            )
        return super().prepare(action_id, settings, inputs)

    async def choices(
        self,
        provider: str,
        query: str,
        settings: PluginSettings,
        context: PluginContext | None = None,
    ) -> list[str]:
        if (
            provider not in {"themes", "commands", "snippets"}
            or not isinstance(settings, Settings)
            or not settings.vault
        ):
            return []
        selected = vault(settings.vault)
        if not await run_sync(running, selected):
            return []
        values = await run_sync(native, selected, provider)
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
            and isinstance(inputs, (Theme, CoreFeature, EditorSnippet))
        ):
            selected = vault(settings.vault)
            if not running(selected):
                return Result(outcome=Outcome.BLOCKED, message="Selected vault is no longer active")
            if isinstance(inputs, Theme):
                native(selected, "theme:set", "name=" + inputs.name)
            elif isinstance(inputs, EditorSnippet):
                native(
                    selected,
                    "snippet:enable" if inputs.enabled else "snippet:disable",
                    "name=" + inputs.name,
                )
            else:
                native(
                    selected,
                    "plugin:enable" if inputs.enabled else "plugin:disable",
                    "id=" + inputs.feature,
                    "filter=core",
                )
        else:
            raise ValueError("Unexpected vault action")
        return Result(
            outcome=Outcome.CHANGED, message="Vault action submitted", resources=plan.resources
        )

    async def verify(
        self,
        plan: Plan,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Result:
        if isinstance(inputs, OpenVault) and isinstance(settings, Settings) and settings.vault:
            try:
                confirmed = await run_sync(running, vault(settings.vault))
            except (ValueError, OSError):
                confirmed = False
            return Result(
                outcome=Outcome.COMPLETE if confirmed else Outcome.NEEDS_USER_ACTION,
                message="Vault opened"
                if confirmed
                else "Check the selected native vault and enable CLI readback in Settings",
            )
        if isinstance(inputs, NewVault):

            def matches_vault() -> bool:
                selected = vault(inputs.path)
                parent = selected.parent.stat()
                return (
                    len(plan.resources) == 1
                    and str(selected) == plan.resources[0].identity
                    and selected.is_dir()
                    and plan.resources[0].after == f"{parent.st_dev}:{parent.st_ino}"
                )

            ok = await run_sync(matches_vault)
        elif isinstance(inputs, (Theme, CoreFeature, EditorSnippet)):
            actions = {
                Theme: PluginAction.SET_THEME,
                CoreFeature: PluginAction.CORE_FEATURE,
                EditorSnippet: PluginAction.EDITOR_SNIPPET,
            }
            result = await self.plan(actions[type(inputs)], settings, inputs)
            ok = result.outcome == Outcome.NO_CHANGES
        else:
            return await super().verify(plan, settings, inputs)
        return Result.observed(
            outcome=Outcome.COMPLETE if ok else Outcome.PARTIAL,
            message="Selected vault state verified" if ok else "Vault state differs",
            resources=plan.resources,
        )
