"""Manage official Codex binaries, scoped configuration, and reviewed instructions."""

import asyncio
import hashlib
import os
import shutil
import tomllib
from pathlib import Path
from typing import ClassVar, Literal

import tomlkit
from pydantic import Field

from setup_system.domain.managed_text import managed_block
from setup_system.infrastructure.file_publication import observe
from setup_system.infrastructure.user_storage import capture_receipt, checked_directory, inside_home
from setup_system_sdk import Inputs, Outcome, Plan, PluginSettings, Result
from setup_system_sdk.native import FilesBackend, command, owned_file_removal, remove_owned_file

from . import client, release


class PolicySource(Inputs):
    repository: Literal["serrja/baseRules"]
    document: Literal["docs/engineering.md"]
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Settings(PluginSettings):
    permission: Literal["inherit","read-only", "workspace-write",":read-only",":workspace"] = Field(
        default="inherit", title="Sandbox or named permission profile"
    )
    approval: Literal["inherit","on-request"] = Field(
        default="inherit", title="Approval policy"
    )
    model: str = Field(
        default="",
        max_length=160,
        title="Model",
        json_schema_extra={"ui": {"choice_provider": "models"}},
    )
    effort: str = Field(
        default="",
        max_length=64,
        title="Reasoning effort",
        json_schema_extra={"ui": {"choice_provider": "efforts"}},
    )
    instructions: bool = Field(default=False, title="Include the pinned engineering instructions")


class Project(Inputs):
    project: Path | None = Field(
        default=None,
        title="Project checkout",
        description="Leave empty for the current user's config.",
    )


def program() -> Path:
    path = shutil.which("codex")
    return Path(path) if path else release.binary()


def original(path: Path) -> bytes:
    inside_home(path)
    return capture_receipt(path).data if path.exists() else b""


class Plugin(FilesBackend):
    executable = "/usr/bin/codex"
    settings_model = Settings
    file_actions = frozenset({"configure"})
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        "check": Inputs,
        "install": Inputs,
        "update": Inputs,
        "uninstall": Inputs,
        "configure": Project,
        "login": Inputs,
        "status": Inputs,
        "logout": Inputs,
        "mcp": Inputs,
        "skills": Inputs,
    }
    manual: ClassVar[dict[str, str]] = {
        "login": (
            "Run codex login in the native terminal and complete its sign-in flow, then "
            "choose Account status."
        ),
        "mcp": (
            "Review a single server's source and permissions. Use codex mcp add in its native "
            "terminal, then verify with codex mcp list."
        ),
        "skills": (
            "Choose the specific skill in the official client's skill manager, review its "
            "source, and enable it for the intended scope."
        ),
    }

    async def inspect(self, settings: PluginSettings) -> Result:
        present = await asyncio.to_thread(lambda: os.access(program(), os.X_OK))
        return Result(
            outcome=Outcome.NO_CHANGES,
            message="Codex available" if present else "Codex executable unavailable",
        )

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id == "uninstall":
            return owned_file_removal(release.binary())
        if action_id in {"install", "update"}:
            return release.plan(action_id, release.resolve())
        if action_id == "check":
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
        if action_id in {"status", "logout"}:
            if not program().is_file():
                return Plan(outcome=Outcome.BLOCKED, summary="Install the official CLI first")
            rc, _ = command(str(program()), "login", "status")
            if rc:
                return Plan(
                    outcome=Outcome.NEEDS_USER_ACTION,
                    summary="Check native CLI authentication status",
                )
            if action_id == "status":
                return Plan(outcome=Outcome.NO_CHANGES, summary="Native Codex login verified")
            return Plan(
                summary="Sign out of this local Codex client",
                warnings=["Your other sessions and server tokens remain valid."],
            )
        return super().prepare(action_id, settings, inputs)

    async def plan(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if (
            action_id == "configure"
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
        return await super().plan(action_id, settings, inputs)

    async def choices(self, provider: str, query: str, settings: PluginSettings) -> list[str]:
        if (
            provider not in {"models", "efforts"}
            or not isinstance(settings, Settings)
            or not program().is_file()
        ):
            return []
        values = await client.models(program())
        options = list(values) if provider == "models" else values.get(settings.model, [])
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
        named="default_permissions" in parsed or "permissions" in parsed
        if settings.permission.startswith(":"):
            if "sandbox_workspace_write" in parsed:
                raise ValueError("Review existing workspace permissions before switching to a named profile")
            parsed.pop("sandbox_mode",None)
            parsed["default_permissions"]=settings.permission
        elif settings.permission!="inherit":
            if named:
                raise ValueError("Review the existing named profile before changing sandbox mode")
            parsed["sandbox_mode"]=settings.permission
        elif not named:
            parsed.setdefault("sandbox_mode","workspace-write")
        if settings.approval!="inherit":
            parsed["approval_policy"]=settings.approval
        else:
            parsed.setdefault("approval_policy","on-request")
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
        if action_id == "uninstall":
            return remove_owned_file(release.binary(), plan)
        if action_id in {"install", "update"}:
            return release.install(action_id, plan)
        if action_id == "logout":
            rc, _ = command(str(program()), "logout")
            return Result(
                outcome=Outcome.UNKNOWN_OUTCOME if rc else Outcome.CHANGED,
                message="Check native Codex authentication after local logout",
            )
        return super().mutate(action_id, settings, inputs, plan)

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        if not plan.resources:
            return Result(
                outcome=Outcome.NEEDS_USER_ACTION,
                message="Run codex login status in the native terminal to confirm local logout",
            )
        if plan.resources[0].identity == str(release.binary()):
            row = plan.resources[0]
            current = await asyncio.to_thread(observe, release.binary())
            ok = (
                current is None
                if row.effect == "remove"
                else bool(
                    current and current.sha256 == row.after and current.mode == row.permissions
                )
            )
            return Result(
                outcome=Outcome.COMPLETE if ok else Outcome.PARTIAL,
                message="Official CLI verified" if ok else "Installed CLI differs",
                resources=plan.resources,
            )
        return await super().verify(plan, settings, inputs)
