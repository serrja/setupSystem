"""Observe registry credential presence without exposing passwords or access tokens."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import Field, TypeAdapter

from setup_system.infrastructure.user_storage import inside_home
from setup_system_sdk import Inputs, Outcome, Plan, Result, user_terminal
from setup_system_sdk.native import command

DOCKER = Path("/usr/bin/docker")


class Location(Inputs):
    helper: str = Field(max_length=128)
    inline: bool


class Registry(Inputs):
    host: str = Field(
        default="docker.io",
        max_length=253,
        pattern=r"^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z0-9-]+(:[0-9]{1,5})?$",
    )


class SignIn(Registry):
    """Read back the credential helper for the server selected at confirmation."""


def store(registry: Registry) -> tuple[str, Literal["present", "absent", "manual"]]:
    path = inside_home(Path.home() / ".docker/config.json")
    if not path.exists():
        return "", "manual"
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Docker client settings exceed the inspection limit")
    # jq returns only the helper identity and a boolean. Secret field values remain in its process.
    key = registry.host if registry.host != "docker.io" else "https://index.docker.io/v1/"
    selector = (
        '{helper:(.credHelpers[$key] // .credsStore // ""),'
        "inline:([.auths[$host],.auths[$key]] | any(. != null and "
        '((.auth // .identitytoken // .registrytoken // "") | length > 0)))}'
    )
    rc, data = command(
        "/usr/bin/jq",
        "-ce",
        "--arg",
        "host",
        registry.host,
        "--arg",
        "key",
        key,
        selector,
        str(path),
        capture=True,
    )
    if rc:
        raise ValueError("Cannot identify the Docker registry credential store")
    location = Location.model_validate_json(data)
    if location.helper not in {"secretservice", "pass"} or location.inline:
        return "", "manual"
    try:
        rc, stored = command("/usr/bin/docker-credential-" + location.helper, "list", capture=True)
    except FileNotFoundError:
        return "", "manual"
    if rc:
        return "", "manual"
    entries = TypeAdapter(dict[str, str]).validate_json(stored)
    return location.helper, "present" if key in entries else "absent"


def presence(registry: Registry) -> Literal["present", "absent", "manual"]:
    return store(registry)[1]


def login_plan(
    inputs: SignIn, inventory: tuple[str, Literal["present", "absent", "manual"]] | None = None
) -> Plan:
    helper, state = inventory if inventory is not None else store(inputs)
    if state == "manual":
        return Plan(
            outcome=Outcome.NEEDS_USER_ACTION,
            summary="Configure a working pass or secretservice credential helper for "
            f"{inputs.host} and move any inline credentials to it before signing in.",
        )
    if state == "present":
        return Plan(
            outcome=Outcome.NO_CHANGES,
            summary="Local registry credential exists. Sign out before choosing another account.",
        )
    if not user_terminal.available() or not DOCKER.is_file():
        return Plan(
            outcome=Outcome.NEEDS_USER_ACTION,
            summary=f"Use the official docker login {inputs.host} in a terminal with the "
            f"configured {helper} credential helper.",
        )
    return Plan(summary=f"Sign into {inputs.host} with Docker using the {helper} credential helper")


def login(inputs: SignIn, plan: Plan) -> Result:
    helper, state = store(inputs)
    if not helper or state == "manual" or login_plan(inputs, (helper, state)) != plan:
        return Result(outcome=Outcome.BLOCKED, message="The reviewed credential helper changed")
    # Docker only sees the reviewed store, even if its usual config changes during sign-in.
    with TemporaryDirectory(prefix="setup-system-docker-login-") as directory:
        config = Path(directory) / "config.json"
        config.write_text(json.dumps({"credsStore": helper}), encoding="utf-8")
        argv = [str(DOCKER), "--config", directory, "login"]
        if inputs.host != "docker.io":
            argv.append(inputs.host)
        rc = user_terminal.run_native(argv)
    actual_helper, observed = store(inputs)
    ok = rc == 0 and actual_helper == helper and observed == "present"
    return Result(
        outcome=Outcome.CHANGED if ok else Outcome.NEEDS_USER_ACTION,
        message="Docker authenticated and the selected registry has a stored credential"
        if ok
        else "Review the native Docker sign-in and its credential helper",
    )
