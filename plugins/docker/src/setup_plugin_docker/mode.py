"""Bind a normal-user Docker CLI launcher to a verified local Engine and context."""

import asyncio
import hashlib
import os
import pwd
import re
import stat
from pathlib import Path
from typing import Literal

from pydantic import Field, TypeAdapter

from setup_plugin_docker.enums import DockerAction
from setup_plugin_docker.settings import Settings
from setup_system.execution.recipes.managed_assets import PublishedAssets
from setup_system.execution.recipes.native import command
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan, Result

LAUNCHER_SOURCE = Path(__file__).with_name("selected_daemon.sh")
DOCKER = "/usr/bin/docker"
SYSTEMCTL = "/usr/bin/systemctl"
CONTEXT_NAME = r"^[a-z][a-z0-9_-]{0,63}$"
ENDPOINT_FORMAT = "{{.Endpoints.docker.Host}}"
ENGINE_FORMAT = "{{.ID}}"
OPTIONS_FORMAT = "{{json .SecurityOptions}}"


class DaemonMode(Inputs):
    context: str | None = Field(
        default=None,
        pattern=CONTEXT_NAME,
        title="Existing local Docker context",
        description="Use the rootless or default context when unselected.",
        json_schema_extra={"ui": {"choice_provider": "mode_contexts"}},
    )


def endpoint(settings: Settings) -> str:
    return (
        f"unix:///run/user/{os.getuid()}/docker.sock"
        if settings.mode == "rootless"
        else "unix:///var/run/docker.sock"
    )


def socket_path(settings: Settings) -> Path:
    return (
        Path(f"/run/user/{os.getuid()}/docker.sock")
        if settings.mode == "rootless"
        else Path("/run/docker.sock")
    )


def contexts(settings: Settings, query: str) -> list[str]:
    try:
        rc, output = command(
            DOCKER, "context", "ls", "--format", "{{.Name}}\t{{.DockerEndpoint}}", capture=True
        )
    except OSError:
        return []
    accepted = {endpoint(settings), f"unix://{socket_path(settings)}"}
    return (
        []
        if rc
        else [
            name
            for line in output.splitlines()
            for name, _, host in [line.partition("\t")]
            if host in accepted and re.fullmatch(CONTEXT_NAME, name) and query.casefold() in name
        ][:128]
    )


def bootstrap(settings: Settings) -> Plan:
    if settings.mode == "rootful":
        return Plan(
            outcome=Outcome.NEEDS_USER_ACTION,
            summary="Inspect docker.service with your administrator. Install Docker if its unit is "
            "missing, then review Start system service. Review local socket access separately.",
        )
    issues = []
    for name in ("newuidmap", "newgidmap"):
        if not os.access("/usr/bin/" + name, os.X_OK):
            issues.append(name)
    account = pwd.getpwuid(os.getuid()).pw_name
    for scope, flags in (("subuid", ()), ("subgid", ("-g",))):
        try:
            rc, output = command("/usr/bin/getsubids", *flags, account, capture=True)
        except OSError:
            rc, output = 1, ""
        if rc or not any(
            len(parts := line.split()) == 4 and parts[-1].isdecimal() and int(parts[-1]) >= 65536
            for line in output.splitlines()
        ):
            issues.append(f"65536 subordinate {scope} IDs")
    return Plan(
        outcome=Outcome.NEEDS_USER_ACTION,
        summary=(
            ("Ask your administrator to verify " + ", ".join(issues) + ". " if issues else "")
            + "Review the packaged dockerd-rootless-setuptool.sh install as your normal user. "
            "It can create a systemd unit and context. Start the user service separately and "
            "return here when its local socket is ready."
        ),
    )


class Selection(Inputs):
    mode: Literal["rootless", "rootful"]
    context: str
    host: str
    engine: str


class Launcher(PublishedAssets):
    file_actions = frozenset({DockerAction.SETUP_MODE})

    def __init__(self, selection: Selection) -> None:
        self.selection = selection

    def mode(self, path: Path) -> int:
        return 0o700 if path.name == "setup-system-docker" else 0o600

    def files(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> dict[Path, bytes]:
        config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        return {
            config
            / "setup-system/docker/selected-daemon.json": self.selection.model_dump_json().encode(),
            Path.home() / ".local/bin/setup-system-docker": LAUNCHER_SOURCE.read_bytes(),
        }


def inspect(settings: Settings, selection: DaemonMode) -> Launcher | Plan:
    context = selection.context or ("default" if settings.mode == "rootful" else "rootless")
    if not os.access(DOCKER, os.X_OK):
        return Plan(outcome=Outcome.NEEDS_USER_ACTION, summary="Install the Docker CLI first.")
    if not os.access("/usr/bin/jq", os.X_OK) or not os.access(SYSTEMCTL, os.X_OK):
        return Plan(outcome=Outcome.NEEDS_USER_ACTION, summary="Verify systemctl and install jq.")
    flags = ("--user",) if settings.mode == "rootless" else ()
    rc, service = command(
        SYSTEMCTL,
        *flags,
        "show",
        "docker.service",
        "--property=LoadState,ActiveState",
        capture=True,
    )
    if rc or "LoadState=loaded" not in service.splitlines():
        return bootstrap(settings)
    if "ActiveState=active" not in service.splitlines():
        return Plan(
            outcome=Outcome.NEEDS_USER_ACTION,
            summary="Inspect docker.service and confirm its separate Start action first.",
        )
    socket = socket_path(settings)
    try:
        info = socket.lstat()
    except FileNotFoundError:
        return Plan(outcome=Outcome.NEEDS_USER_ACTION, summary=f"Check the missing socket {socket}")
    owner = os.getuid() if settings.mode == "rootless" else 0
    if (
        socket.resolve() != socket
        or not stat.S_ISSOCK(info.st_mode)
        or info.st_uid != owner
        or info.st_mode & 0o002
    ):
        return Plan(
            outcome=Outcome.BLOCKED, summary="Review the local socket type, owner and access"
        )
    rc, selected = command(
        DOCKER, "context", "inspect", context, "--format", ENDPOINT_FORMAT, capture=True
    )
    host = selected.strip()
    if rc:
        return Plan(
            outcome=Outcome.NEEDS_USER_ACTION,
            summary=f"Use Create context for {endpoint(settings)} and select its name here.",
        )
    if host not in {endpoint(settings), f"unix://{socket}"}:
        return Plan(
            outcome=Outcome.BLOCKED, summary="The context points to another Docker endpoint"
        )
    rc, data = command(
        DOCKER,
        "--host",
        host,
        "info",
        "--format",
        ENGINE_FORMAT + "\n" + OPTIONS_FORMAT,
        capture=True,
    )
    engine, _, raw = data.strip().partition("\n")
    if rc or not re.fullmatch(r"[A-Za-z0-9:-]{8,128}", engine):
        return Plan(
            outcome=Outcome.BLOCKED, summary="Docker could not verify the selected Engine ID"
        )
    try:
        options = TypeAdapter(list[str]).validate_json(raw)
    except ValueError:
        return Plan(
            outcome=Outcome.BLOCKED, summary="Docker returned unknown Engine security options"
        )
    if ("name=rootless" in options) != (settings.mode == "rootless"):
        return Plan(outcome=Outcome.BLOCKED, summary="The Engine reports a different daemon mode")
    return Launcher(Selection(mode=settings.mode, context=context, host=host, engine=engine))


def prepare(settings: Settings, selection: DaemonMode) -> Plan:
    found = inspect(settings, selection)
    if isinstance(found, Plan):
        return found
    plan = found.prepare(DockerAction.SETUP_MODE, settings, selection)
    if plan.outcome not in (Outcome.READY, Outcome.NO_CHANGES):
        return plan
    if any(row.permissions != found.mode(Path(row.identity)) for row in plan.resources):
        return Plan(outcome=Outcome.BLOCKED, summary="Review the existing launcher permissions")
    selected = found.selection
    return plan.model_copy(
        update={
            "summary": f"Bind setup-system-docker to {selected.mode} Engine {selected.engine} "
            f"using context {selected.context} at {selected.host} (docker.service active)",
            "warnings": ["Commands sent to the rootful socket have root-equivalent power."]
            if settings.mode == "rootful"
            else [],
        }
    )


def mutate(settings: Settings, selection: DaemonMode, plan: Plan) -> Result:
    found = inspect(settings, selection)
    if isinstance(found, Plan) or plan.outcome != Outcome.READY:
        return Result(outcome=Outcome.BLOCKED, message="Docker service or context changed")
    return found.mutate(DockerAction.SETUP_MODE, settings, selection, plan)


async def verify(settings: Settings, selection: DaemonMode, plan: Plan) -> Result:
    found = await asyncio.to_thread(inspect, settings, selection)
    if isinstance(found, Launcher):
        desired = {
            str(path): hashlib.sha256(content).hexdigest()
            for path, content in found.files(DockerAction.SETUP_MODE, settings, selection).items()
        }
        if desired == {r.identity: r.after for r in plan.resources}:
            return await found.verify(plan, settings, selection)
    return Result(
        outcome=Outcome.PARTIAL, message="Review the launcher, context and Engine identity"
    )
