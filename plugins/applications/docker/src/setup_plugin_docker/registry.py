"""Observe registry credential presence without exposing passwords or access tokens."""

from pathlib import Path
from typing import Literal

from pydantic import Field, TypeAdapter

from setup_system.infrastructure.user_storage import inside_home
from setup_system_sdk import Inputs
from setup_system_sdk.native import command


class Location(Inputs):
    helper: str = Field(max_length=128)
    inline: bool


class Registry(Inputs):
    host: str = Field(
        default="docker.io",
        max_length=253,
        pattern=r"^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z0-9-]+(:[0-9]{1,5})?$",
    )


def presence(registry: Registry) -> Literal["present", "absent", "manual"]:
    path = inside_home(Path.home() / ".docker/config.json")
    if not path.exists():
        return "manual"
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Docker client settings exceed the inspection limit")
    # jq returns only the helper identity and a boolean. Secret field values remain in its process.
    key = registry.host if registry.host != "docker.io" else "https://index.docker.io/v1/"
    selector = (
        '{helper:(.credHelpers[$host] // .credHelpers[$key] // .credsStore // ""),'
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
        return "manual"
    rc, stored = command("/usr/bin/docker-credential-" + location.helper, "list", capture=True)
    if rc:
        return "manual"
    entries = TypeAdapter(dict[str, str]).validate_json(stored)
    return "present" if key in entries or registry.host in entries else "absent"
