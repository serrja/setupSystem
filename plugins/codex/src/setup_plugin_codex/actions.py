"""Manage official Codex binaries, scoped configuration, and reviewed instructions."""

import hashlib
import os
import shlex
import shutil
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

import tomlkit
from setup_system_sdk.blocking import run_sync
from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.files.backend import (
    FilesBackend,
    command,
    owned_file_removal,
    remove_owned_file,
)
from setup_system_sdk.files.file_publication import observe
from setup_system_sdk.files.managed_text import managed_block
from setup_system_sdk.files.user_storage import capture_receipt, checked_directory, inside_home
from setup_system_sdk.plugin import PluginSettings, owned_resources
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan, Result

from setup_plugin_codex.adapters import user_terminal
from setup_plugin_codex.enums import ClientAction
from setup_plugin_codex.models import McpServer, PolicySource, Project, Settings, SignIn

from . import client, release


def keyring_command(path: Path, *arguments: str) -> list[str]:
    return [str(path), "-c", 'cli_auth_credentials_store="keyring"', *arguments]


def login_binary() -> Path | None:
    path = release.binary()
    state = observe(path)
    if state and any(
        row.kind == "file"
        and row.identity == str(path)
        and row.after == state.sha256
        and row.permissions == state.mode == 0o700
        for row in owned_resources()
    ):
        return path
    return None


def program() -> Path:
    path = shutil.which("codex")
    return Path(path) if path else release.binary()


def original(path: Path) -> bytes:
    inside_home(path)
    return capture_receipt(path).data if path.exists() else b""


class PluginActions(FilesBackend):
    executable = "/usr/bin/codex"

    file_actions = frozenset({LifecycleAction.CONFIGURE, ClientAction.CONFIGURE_MCP})

    manual: ClassVar[dict[StrEnum, str]] = {
        ClientAction.MCP: (
            "Review a single server's source and permissions. Use codex mcp add in its native "
            "terminal, then verify with codex mcp list."
        ),
        ClientAction.SKILLS: (
            "Choose the specific skill in the official client's skill manager, review its "
            "source, and enable it for the intended scope."
        ),
    }

    async def inspect(
        self, settings: PluginSettings, context: PluginContext | None = None
    ) -> Result:
        present = await run_sync(lambda: os.access(program(), os.X_OK))
        return Result(
            outcome=Outcome.NO_CHANGES,
            message="Codex available" if present else "Codex executable unavailable",
        )

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id == LifecycleAction.UNINSTALL:
            return owned_file_removal(release.binary())
        if action_id in {LifecycleAction.INSTALL, LifecycleAction.UPDATE}:
            return release.plan(action_id, release.resolve())
        if action_id == LifecycleAction.CHECK:
            path = program()
            if not path.is_file():
                return Plan(outcome=Outcome.BLOCKED, summary="Codex executable unavailable")
            rc, version = command(str(path), "--version", capture=True)
            return Plan(
                outcome=Outcome.NO_CHANGES if rc == 0 else Outcome.BLOCKED,
                summary="Codex CLI verified"
                if rc == 0 and version.strip()
                else "Version unavailable",
            )
        if action_id == ClientAction.CONFIGURE_MCP and isinstance(inputs, McpServer):
            plan = super().prepare(action_id, settings, inputs)
            return plan.model_copy(
                update={
                    "warnings": [
                        f"Review {inputs.url.host} before enabling its tools. "
                        "Use native credential storage."
                    ]
                }
            )
        if isinstance(inputs, SignIn) and action_id == ClientAction.LOGIN:
            selected = login_binary()
            if not selected:
                return Plan(
                    outcome=Outcome.NEEDS_USER_ACTION,
                    summary="Install the verified official release here or run codex login "
                    "in the terminal of your separately trusted installation.",
                )
            rc, _ = command(*keyring_command(selected, "login", "status"))
            if rc == 0:
                return Plan(outcome=Outcome.NO_CHANGES, summary="Native Codex sign-in is active")
            if not user_terminal.available():
                native_login = shlex.join(keyring_command(selected, "login"))
                return Plan(
                    outcome=Outcome.NEEDS_USER_ACTION,
                    summary=f"Run {native_login} in a terminal. Then choose Account status.",
                )
            return Plan(
                summary="Sign into the verified Codex client using its system keyring",
                warnings=["Interactive Codex sessions must select the same keyring storage mode."],
            )
        if action_id in {ClientAction.STATUS, ClientAction.LOGOUT}:
            if not program().is_file():
                return Plan(outcome=Outcome.BLOCKED, summary="Install the official CLI first")
            managed = login_binary()
            argv = (
                keyring_command(managed, "login", "status")
                if managed
                else [str(program()), "login", "status"]
            )
            rc, _ = command(*argv)
            if rc:
                return Plan(
                    outcome=Outcome.NEEDS_USER_ACTION,
                    summary="Check native CLI authentication status",
                )
            if action_id == ClientAction.STATUS:
                return Plan(outcome=Outcome.NO_CHANGES, summary="Native Codex login verified")
            return Plan(
                summary="Sign out of this local Codex client",
                warnings=["Your other sessions and server tokens remain valid."],
            )
        return super().prepare(action_id, settings, inputs)

    async def plan(
        self,
        action_id: str,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Plan:
        if (
            action_id == LifecycleAction.CONFIGURE
            and isinstance(settings, Settings)
            and (settings.model or settings.effort)
        ):
            available = await client.models(program())
            if settings.model not in available or (
                settings.effort and settings.effort not in available[settings.model]
            ):
                return Plan(
                    outcome=Outcome.BLOCKED,
                    summary=(
                        "Model or reasoning effort unavailable for the current client and account"
                    ),
                )
        return await super().plan(action_id, settings, inputs, context)

    async def choices(
        self,
        provider: str,
        query: str,
        settings: PluginSettings,
        context: PluginContext | None = None,
    ) -> list[str]:
        if (
            provider not in {"models", "efforts"}
            or not isinstance(settings, Settings)
            or not program().is_file()
        ):
            return []
        values = await client.models(program())
        options = list(values) if provider == "models" else values.get(settings.model or "", [])
        return [o for o in options if query.casefold() in o.casefold()][:64]

    def files(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> dict[Path, bytes]:
        if not isinstance(settings, Settings) or not isinstance(inputs, Project):
            raise ValueError("Unexpected Codex configuration")
        if inputs.project:
            project = checked_directory(inside_home(inputs.project))
            if not (project / ".git").is_dir() or (project / ".git").is_symlink():
                raise ValueError("Select an existing Git checkout")
            config = project / ".codex"
            instructions = project
        else:
            config = inside_home(Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
            if config == Path.home():
                raise ValueError("Select a dedicated Codex configuration directory")
            instructions = config
        path = config / "config.toml"
        parsed = tomlkit.parse(original(path).decode())
        if isinstance(inputs, McpServer):
            servers = parsed.setdefault("mcp_servers", tomlkit.table())
            if not isinstance(servers, dict):
                raise ValueError("Existing MCP server configuration requires review")
            entry = servers.setdefault(inputs.name, tomlkit.table())
            if (
                not isinstance(entry, dict)
                or set(entry) - {"url", "enabled"}
                or entry.get("url", str(inputs.url)) != str(inputs.url)
            ):
                raise ValueError("An existing server identity or credentials require native review")
            entry.update({"url": str(inputs.url), "enabled": inputs.enable})
            return {path: tomlkit.dumps(parsed).encode()}
        named = "default_permissions" in parsed or "permissions" in parsed
        if settings.permission.startswith(":"):
            if "sandbox_workspace_write" in parsed:
                raise ValueError(
                    "Review existing workspace permissions before switching to a named profile"
                )
            parsed.pop("sandbox_mode", None)
            parsed["default_permissions"] = settings.permission
        elif settings.permission != "inherit":
            if named:
                raise ValueError("Review the existing named profile before changing sandbox mode")
            parsed["sandbox_mode"] = settings.permission
        elif not named:
            parsed.setdefault("sandbox_mode", "workspace-write")
        if settings.approval != "inherit":
            parsed["approval_policy"] = settings.approval
        else:
            parsed.setdefault("approval_policy", "on-request")
        if settings.model:
            parsed["model"] = settings.model
        if settings.effort:
            parsed["model_reasoning_effort"] = settings.effort
        desired = {path: tomlkit.dumps(parsed).encode()}
        if settings.instructions:
            folder = Path(__file__).parent / "resources"
            policy = (folder / "engineering.md").read_bytes()
            lock = PolicySource.model_validate(tomllib.loads((folder / "rules.toml").read_text()))
            if hashlib.sha256(policy).hexdigest() != lock.sha256:
                raise ValueError("Engineering rules differ from the reviewed source")
            rule = instructions / "AGENTS.override.md"
            if not original(rule).strip():
                rule = instructions / "AGENTS.md"
            url = f"https://github.com/{lock.repository}/blob/{lock.commit}/{lock.document}"
            body = policy.decode().rstrip() + f"\n\n[Reviewed engineering instructions]({url})"
            desired[rule] = managed_block(original(rule).decode(), body).encode()
        return desired

    def mutate(
        self, action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan
    ) -> Result:
        if action_id == LifecycleAction.UNINSTALL:
            return remove_owned_file(release.binary(), plan)
        if action_id in {LifecycleAction.INSTALL, LifecycleAction.UPDATE}:
            return release.install(action_id, plan)
        if isinstance(inputs, SignIn) and action_id == ClientAction.LOGIN:
            path = login_binary()
            if not path:
                return Result(outcome=Outcome.BLOCKED, message="Codex release identity changed")
            rc = user_terminal.run_native(keyring_command(path, "login"))
            status, _ = command(*keyring_command(path, "login", "status"))
            return Result(
                outcome=Outcome.CHANGED if rc == status == 0 else Outcome.NEEDS_USER_ACTION,
                message="Native Codex account verified"
                if rc == status == 0
                else "Check the account in the native Codex terminal",
            )
        if action_id == ClientAction.LOGOUT:
            managed = login_binary()
            argv = keyring_command(managed, "logout") if managed else [str(program()), "logout"]
            rc, _ = command(*argv)
            return Result(
                outcome=Outcome.UNKNOWN_OUTCOME if rc else Outcome.CHANGED,
                message="Check native Codex authentication after local logout",
            )
        return super().mutate(action_id, settings, inputs, plan)

    async def verify(
        self,
        plan: Plan,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Result:
        if isinstance(inputs, SignIn):
            path = await run_sync(login_binary)
            if not path:
                return Result(outcome=Outcome.BLOCKED, message="Codex release identity changed")
            rc, _ = await run_sync(command, *keyring_command(path, "login", "status"))
            return Result(
                outcome=Outcome.COMPLETE if rc == 0 else Outcome.NEEDS_USER_ACTION,
                message="Native Codex account verified" if rc == 0 else "Check native sign-in",
            )
        if not plan.resources:
            return Result(
                outcome=Outcome.NEEDS_USER_ACTION,
                message="Run codex login status in the native terminal to confirm local logout",
            )
        if plan.resources[0].identity == str(release.binary()):
            row = plan.resources[0]
            current = await run_sync(observe, release.binary())
            ok = (
                current is None
                if row.effect == "remove"
                else bool(
                    current and current.sha256 == row.after and current.mode == row.permissions
                )
            )
            return Result.observed(
                outcome=Outcome.COMPLETE if ok else Outcome.PARTIAL,
                message="Official CLI verified" if ok else "Installed CLI differs",
                resources=plan.resources,
            )
        return await super().verify(plan, settings, inputs)
