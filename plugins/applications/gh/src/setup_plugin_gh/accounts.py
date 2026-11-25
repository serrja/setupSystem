"""Discover public account names and verify scoped GitHub CLI actions."""

import os
import re
import stat
from pathlib import Path
from typing import Annotated

import httpx
import yaml
from pydantic import BaseModel, Field
from yaml.events import AliasEvent, CollectionEndEvent, CollectionStartEvent
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from setup_system.infrastructure.user_storage import inside_home, private_directory
from setup_system_sdk import Inputs, Outcome, Plan, Result
from setup_system_sdk.native import command

GH = "/usr/bin/gh"
NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$")
AccountName = Annotated[str, Field(min_length=1, max_length=100, pattern=NAME.pattern)]


class Selection(Inputs):
    host: str = Field(default="github.com", min_length=1, max_length=253)
    account: AccountName


class Identity(BaseModel):
    login: AccountName


MAX_BYTES = 256 * 1024


def mapping(node: Node) -> dict[str, Node]:
    if not isinstance(node, MappingNode):
        raise ValueError("Unexpected GitHub CLI account metadata")
    result: dict[str, Node] = {}
    for key, value in node.value:
        if not isinstance(key, ScalarNode) or key.value in result or key.value == "<<":
            raise ValueError("Ambiguous GitHub CLI account metadata")
        result[key.value] = value
    return result


def unambiguous(node: Node) -> None:
    if isinstance(node, MappingNode):
        for value in mapping(node).values():
            unambiguous(value)
    elif isinstance(node, SequenceNode):
        for element in node.value:
            unambiguous(element)


def _local_names(host: str) -> list[str]:
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "gh"
    with private_directory(inside_home(config), create=False) as folder:
        fd = os.open("hosts.yml", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or info.st_mode & 0o7077
                or info.st_size > MAX_BYTES
            ):
                raise ValueError("Expected a private, owned GitHub CLI account file")
            raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Oversized account metadata")
    depth = 0
    for index, event in enumerate(yaml.parse(raw, Loader=yaml.BaseLoader)):
        if (
            isinstance(event, AliasEvent)
            or getattr(event, "anchor", None)
            or getattr(event, "tag", None)
        ):
            raise ValueError("Ambiguous account metadata")
        depth += isinstance(event, CollectionStartEvent) - isinstance(event, CollectionEndEvent)
        if depth > 16 or index > 8192:
            raise ValueError("Excessive account metadata")
    root = yaml.compose(raw, Loader=yaml.BaseLoader)
    if root is None:
        return []
    unambiguous(root)
    host_node = mapping(root).get(host)
    if host_node is None:
        return []
    fields = mapping(host_node)
    active = fields.get("user")
    users = fields.get("users")
    names = list(mapping(users)) if users is not None else []
    if active is not None:
        if not isinstance(active, ScalarNode) or (names and active.value not in names):
            raise ValueError("Unknown active GitHub account")
        if not names:
            names = [active.value]
    if not names or any(not NAME.fullmatch(name) for name in names):
        raise ValueError("Unrecognized GitHub account inventory")
    if len(set(name.casefold() for name in names)) != len(names) or len(names) > 128:
        raise ValueError("Ambiguous GitHub account inventory")
    return sorted(names)


def local_names(host: str) -> list[str]:
    """Return only public names. YAML diagnostics may include source lines containing tokens."""
    try:
        return _local_names(host)
    except FileNotFoundError:
        return []
    except (OSError, ValueError, yaml.YAMLError):
        raise ValueError("Cannot safely inspect the local GitHub CLI account inventory") from None


def observed_names(host: str) -> list[str] | None:
    names = local_names(host)
    rc, text = command(GH, "auth", "status", "--help", capture=True)
    if rc:
        return None
    flags = text.split()
    if not names or "--json" not in flags:
        return names
    argv = [GH, "auth", "status", "--hostname", host]
    if "--no-refresh" in flags:
        argv.append("--no-refresh")
    rc, listed = command(*argv, "--json", "hosts", "--jq", ".hosts[][] | .login", capture=True)
    return names if not rc and sorted(listed.splitlines()) == names else None


def selector_supported() -> bool:
    rc, text = command(GH, "auth", "logout", "--help", capture=True)
    return rc == 0 and {"--hostname", "--user"} <= set(text.split())


def named_account(inputs: Selection) -> bool:
    rc, token = command(
        GH, "auth", "token", "--hostname", inputs.host, "--user", inputs.account, capture=True
    )
    if rc or not token.strip() or not token.strip().isascii():
        return False
    root = (
        "https://api.github.com" if inputs.host == "github.com" else f"https://{inputs.host}/api/v3"
    )
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as api:
        with api.stream(
            "GET", root + "/user", headers={"Authorization": "Bearer " + token.strip()}
        ) as response:
            if response.status_code != 200:
                return False
            content = bytearray()
            for chunk in response.iter_bytes():
                if len(content) + len(chunk) > 65536:
                    return False
                content.extend(chunk)
    return Identity.model_validate_json(content).login.casefold() == inputs.account.casefold()


def authenticated(inputs: Selection) -> bool:
    try:
        rc, active = command(
            GH, "api", "--hostname", inputs.host, "user", "--jq", ".login", capture=True
        )
        if rc == 0 and active.strip().casefold() == inputs.account.casefold():
            return True
        # Obtain the inactive account through the CLI without switching the active user.
        return named_account(inputs)
    except (ValueError, OSError, httpx.HTTPError):
        return False


def online(host: str, names: list[str]) -> bool:
    if names:
        rc, login = command(GH, "api", "--hostname", host, "user", "--jq", ".login", capture=True)
        return rc == 0 and login.strip().casefold() in {name.casefold() for name in names}
    # After the last local sign-out there is no credential left for gh api.
    base = "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
    try:
        with httpx.Client(follow_redirects=False, trust_env=False, timeout=5) as api:
            with api.stream("GET", base + "/rate_limit") as reply:
                return reply.status_code == 200 and bool(reply.headers.get("x-github-request-id"))
    except httpx.HTTPError:
        return False


def selected_name(names: list[str], inputs: Selection) -> str | None:
    return next((name for name in names if name.casefold() == inputs.account.casefold()), None)


def prepare(inputs: Selection, *, logout: bool) -> Plan:
    names = observed_names(inputs.host)
    if names is None:
        return Plan(
            outcome=Outcome.NEEDS_USER_ACTION, summary="Recheck the native GitHub account inventory"
        )
    selected = selected_name(names, inputs)
    if not selected:
        if logout and online(inputs.host, names):
            return Plan(outcome=Outcome.NO_CHANGES, summary="The selected local account is absent")
        return Plan(
            outcome=Outcome.NEEDS_USER_ACTION, summary="Check account selection and host access"
        )
    if logout and not selector_supported():
        return Plan(
            outcome=Outcome.NEEDS_USER_ACTION,
            summary="Install a GitHub CLI release with gh auth logout --hostname and --user "
            f"to remove only {selected} on {inputs.host}.",
        )
    if not authenticated(inputs.model_copy(update={"account": selected})):
        return Plan(
            outcome=Outcome.NEEDS_USER_ACTION,
            summary="Selected account authentication could not be verified",
        )
    return Plan(
        outcome=Outcome.READY if logout else Outcome.NO_CHANGES,
        summary=f"Sign out {selected} on {inputs.host}"
        if logout
        else f"Verified {selected} on {inputs.host}",
        warnings=["This removes the local CLI account. Remote grants remain valid."]
        if logout
        else [],
    )


def verify_logout(inputs: Selection) -> Result:
    names = observed_names(inputs.host)
    if names is None:
        return Result(
            outcome=Outcome.NEEDS_USER_ACTION, message="Recheck the native GitHub account inventory"
        )
    if selected_name(names, inputs):
        return Result(outcome=Outcome.PARTIAL, message="The selected account is still configured")
    if not online(inputs.host, names):
        return Result(
            outcome=Outcome.NEEDS_USER_ACTION,
            message="The account is locally absent. Recheck GitHub host access.",
        )
    return Result(
        outcome=Outcome.COMPLETE,
        message="Verified local removal of the selected account and host access",
    )
