"""Open a reviewed checkout in a user-session terminal owned by systemd."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from setup_system.execution.recipes.native import command
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.resources import ResourceOwnership
from setup_system_sdk.schemas.operation import Plan, Resource

from .project_layout import active_projects, load_layout

PROGRAMS = {"open-editor": "nvim", "open-codex": "codex"}
TERMINAL = Path("/usr/bin/foot")


def paths() -> dict[str, Path]:
    return active_projects(load_layout(Path(__file__).parent))


def binary(program: str) -> Path:
    for path in (Path.home() / ".local/bin" / program, Path("/usr/bin") / program):
        if path.is_file() and os.access(path, os.X_OK):
            return path
    raise ValueError("Install the selected program first.")


def session_plan(action: str, project: str) -> Plan:
    destination = paths().get(project)
    if not destination:
        raise ValueError("Choose an active project with its canonical origin.")
    program = binary(PROGRAMS[action])
    display = os.environ.get("WAYLAND_DISPLAY", "")
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if not runtime or not display or Path(display).name != display:
        return Plan(
            outcome=Outcome.NEEDS_USER_ACTION,
            summary=f"In a local terminal, open {destination} and start {program.name}.",
        )
    if not TERMINAL.is_file() or not (Path(runtime) / display).is_socket():
        return Plan(
            outcome=Outcome.BLOCKED,
            summary="A running local Wayland session and Foot terminal are required.",
        )
    if os.stat(runtime).st_uid != os.getuid() or stat.S_IMODE(os.stat(runtime).st_mode) != 0o700:
        raise ValueError("The runtime directory must belong exclusively to the session user.")
    name = hashlib.sha256(f"{destination}/{program}".encode()).hexdigest()[:20]
    unit = f"setup-system-project-{name}.service"
    status, observed = command(
        "/usr/bin/systemctl",
        "--user",
        "show",
        "--property=ActiveState",
        "--value",
        unit,
        capture=True,
    )
    state = observed.strip()
    if status not in (0, 4) or state not in ("active", "inactive", "failed"):
        return Plan(
            outcome=Outcome.BLOCKED,
            summary="The user service manager is unavailable or the terminal unit is changing.",
        )
    return Plan(
        outcome=Outcome.NO_CHANGES if state == "active" else Outcome.READY,
        summary=f"Open {project} in {program.name}.",
        resources=[
            Resource(
                kind="user-session",
                identity=unit,
                before=state or "unknown",
                after="active",
                ownership=ResourceOwnership.CREATED,
            ),
            Resource(
                kind="project", identity=str(destination), ownership=ResourceOwnership.PREEXISTING
            ),
            Resource(
                kind="executable", identity=str(program), ownership=ResourceOwnership.PREEXISTING
            ),
        ],
    )


def start(plan: Plan) -> None:
    service, destination, program = plan.resources
    args = [
        "/usr/bin/systemd-run",
        "--user",
        "--quiet",
        "--collect",
        "--service-type=exec",
        "--unit=" + service.identity,
        "--working-directory=" + destination.identity,
        "--setenv=WAYLAND_DISPLAY=" + os.environ["WAYLAND_DISPLAY"],
        "--",
        str(TERMINAL),
        program.identity,
    ]
    if Path(program.identity).name == "nvim":
        args.append(".")
    status, _ = command(*args)
    if status:
        raise ValueError("The user service manager could not start the project terminal.")


def active(unit: str) -> bool:
    return command("/usr/bin/systemctl", "--user", "is-active", "--quiet", unit)[0] == 0
