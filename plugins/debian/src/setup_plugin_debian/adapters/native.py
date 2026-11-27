"""Run one approved package-owned Ansible play with native terminal authentication."""

from __future__ import annotations

import base64
import os
import re
import subprocess
from tempfile import TemporaryDirectory
from typing import Literal, Protocol

from pydantic import JsonValue, TypeAdapter
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan, Result

from setup_plugin_debian.adapters.layout import (
    INSTALLED,
    NATIVE_ANSIBLE,
    NATIVE_PYTHON,
    require_installed_resource,
)

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
        playbook not in {"@apt", "@cleanup"} and not re.fullmatch(r"[a-z][a-z0-9_]*\.yml", playbook)
    ):
        raise ValueError("Invalid protected plugin playbook identity.")
    expected = (
        f"plugins/{plugin_id}/setup_plugin_{plugin_id.replace('-', '_')}/resources/{playbook}"
    )
    if playbook == "@apt":
        expected = "apt.yml"
    if playbook == "@cleanup":
        expected = "cleanup.yml"
    source = INSTALLED / expected
    require_installed_resource(source, expected)
    with TemporaryDirectory(prefix="setup-system-ansible-") as stage:
        subprocess.run(
            [
                str(NATIVE_PYTHON),
                "-I",
                "-B",
                str(NATIVE_ANSIBLE),
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
