"""Run one approved package-owned Ansible play with native terminal authentication."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import os
import re
import stat
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, Protocol

from pydantic import JsonValue, TypeAdapter

from setup_system.execution.operation_io import read_wire
from setup_system.layout import INSTALLED, require_installed_resource
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.plugin import Backend, PluginSettings, ownership_context
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.manifest import Manifest
from setup_system_sdk.schemas.operation import Mutation, Plan, Result

_LIMIT = 262144
_EXTRA = TypeAdapter(dict[str, JsonValue])


class PrivilegedChange(Protocol):
    def __call__(
        self, action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan
    ) -> Result | None: ...


def public_parameters(extra: dict[str, JsonValue]) -> str:
    """Encode the non-secret plan as one bounded, immutable Ansible argument."""
    content = _EXTRA.dump_json(extra)
    if len(content) > 65536:
        raise ValueError("Ansible parameters exceed the supported size.")
    # Ansible trusts extra variables as templates. Only the fixed raw adapter
    # decodes these bytes, in the input pipe of the installed native helper.
    return "setup_system_payload=" + base64.b64encode(content).decode("ascii")


def run_native_play(
    plugin_id: str, playbook: str, extra: dict[str, JsonValue], method: Literal["su", "sudo"]
) -> None:
    """Authenticate in the caller's PTY and isolate the public Ansible environment."""
    if os.geteuid() == 0 or not os.isatty(0):
        raise ValueError("A normal-user authorization terminal is required.")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", plugin_id) or (
        playbook != "@apt" and not re.fullmatch(r"[a-z][a-z0-9_]*\.yml", playbook)
    ):
        raise ValueError("Invalid protected plugin playbook identity.")
    source = Path.cwd() / playbook
    expected = f"plugins/{plugin_id}/setup_plugin_{plugin_id.replace('-', '_')}/{playbook}"
    if playbook == "@apt":
        expected = "sdk/apt.yml"
        source = INSTALLED / expected
    require_installed_resource(source, expected)
    with TemporaryDirectory(prefix="setup-system-ansible-") as stage:
        subprocess.run(
            [
                "/usr/bin/python3",
                "-I",
                "-B",
                "/usr/bin/ansible-playbook",
                "-i",
                "localhost,",
                "-c",
                "local",
                "--become-method",
                method,
                "--ask-become-pass",
                "--extra-vars",
                public_parameters(extra),
                str(source),
            ],
            cwd=INSTALLED,
            env={
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "HOME": stage,
                "LC_ALL": "C",
                "ANSIBLE_CONFIG": str(INSTALLED / "ansible.cfg"),
                "ANSIBLE_HOME": "/usr/share/ansible",
                "ANSIBLE_LOCAL_TEMP": stage + "/local",
                "ANSIBLE_REMOTE_TEMP": stage + "/remote",
                "ANSIBLE_NOCOLOR": "1",
                "ANSIBLE_NOCOWS": "1",
            },
            check=True,
        )


@contextmanager
def private_exchange() -> Iterator[tuple[Mutation, int]]:
    """Address both files through the same verified private directory descriptor."""
    request = Path(os.environ["SETUP_SYSTEM_REQUEST"])
    result = Path(os.environ["SETUP_SYSTEM_RESULT"])
    if (
        request.name != "request.json"
        or result != request.with_name("result.json")
        or not request.is_absolute()
        or request.parent.resolve(strict=True) != request.parent
    ):
        raise ValueError("Unexpected native request and result paths.")
    directory = os.open(request.parent, os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        meta = os.fstat(directory)
        if meta.st_uid != os.getuid() or stat.S_IMODE(meta.st_mode) != 0o700:
            raise ValueError("Native authorization needs a private operation directory.")
        held = Path("/proc/self/fd") / str(directory)
        if os.path.lexists(held / result.name):
            raise ValueError("The result already exists.")
        yield (
            Mutation.model_validate_json(
                read_wire(held / request.name, limit=_LIMIT, private=True)
            ),
            directory,
        )
    finally:
        os.close(directory)


async def execute(
    backend: Backend, change: PrivilegedChange, action: str, request: Mutation
) -> Result:
    if request.action_id != action or action not in backend.commands():
        return Result(outcome=Outcome.BLOCKED, message="Unexpected privileged action.")
    with ownership_context(request.holdings):
        settings = backend.settings_model.model_validate(request.settings)
        command = backend.command(action)
        inputs = command.validate(request.inputs)
        observed = await command.plan(settings, inputs)
        if observed != request.plan or observed.outcome != Outcome.READY:
            return Result(
                outcome=Outcome.BLOCKED, message="The confirmed plan changed. Preview again."
            )
        try:
            evidence = await asyncio.to_thread(change, action, settings, inputs, observed)
            if evidence is not None:
                return evidence
            return await command.readback(observed, settings, inputs)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            return Result(
                outcome=Outcome.UNKNOWN_OUTCOME,
                message="Read back the native state before retrying.",
            )


def main(plugin_id: str, action: str) -> None:
    """Import the Debian-owned backend only after verifying the installed tree."""
    if (
        os.geteuid() == 0
        or not os.isatty(0)
        or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", plugin_id)
    ):
        raise ValueError("An installed package and authorization terminal are required.")
    module_name = "setup_plugin_" + plugin_id.replace("-", "_")
    root = INSTALLED / "plugins" / plugin_id / module_name
    source = root / "__init__.py"
    if Path.cwd() != root:
        raise ValueError("Unexpected protected resource location.")
    require_installed_resource(source, f"plugins/{plugin_id}/{module_name}/__init__.py")
    manifest = Manifest.model_validate_json((root / "manifest.json").read_bytes())
    descriptor = next((item for item in manifest.actions if item.id == action), None)
    if (
        manifest.id != plugin_id
        or manifest.entry_point != f"{module_name}.plugin:Plugin"
        or descriptor is None
        or "root" not in descriptor.permissions
        or descriptor.target != action
    ):
        raise ValueError("This action has no approved privileged target.")
    spec = importlib.util.spec_from_file_location(
        module_name, source, submodule_search_locations=[str(root)]
    )
    if not spec or not spec.loader:
        raise ValueError("Protected backend unavailable.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    module = importlib.import_module(module_name + ".plugin")
    backend = module.Plugin()
    if not isinstance(backend, Backend):
        raise ValueError("Invalid backend entry point.")
    with private_exchange() as (request, directory):
        result = asyncio.run(execute(backend, module.apply_native, action, request))
        fd = os.open(
            "result.json",
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        with os.fdopen(fd, "w") as stream:
            stream.write(result.model_dump_json())


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(2)
    main(sys.argv[1], sys.argv[2])
