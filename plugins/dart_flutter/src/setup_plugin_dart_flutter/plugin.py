"""Clone the official stable Flutter commit without touching existing SDKs or projects."""

import re
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field
from setup_system_sdk import Inputs, PluginSettings
from setup_system_sdk.downloads import DownloadPolicy, read_https
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_toolchain.tools import Toolchain
from setup_system_toolchain.tools import execute as execute_tool


class PluginAction(StrEnum):
    DOCTOR = "doctor"


REPOSITORY = "https://github.com/flutter/flutter.git"


class Commit(Inputs):
    sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    type: Literal["commit"]
    url: str


class Reference(Inputs):
    ref: Literal["refs/heads/stable"]
    node_id: str
    url: str
    object: Commit


def execute(argv: list[str], stage: Path, *, capture: bool = False) -> str:
    return execute_tool(
        argv,
        stage,
        capture=capture,
        environment={
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
        },
    )


class Settings(PluginSettings):
    publish_dart: bool = Field(
        default=True,
        title="Publish Dart command",
        description="Expose the Dart SDK delivered by this Flutter release.",
    )


class Plugin(Toolchain):
    delegated_step_key = "packages"
    delegated_actions: ClassVar[dict[str, str]] = dict.fromkeys(
        (LifecycleAction.INSTALL, LifecycleAction.UPDATE, LifecycleAction.UNINSTALL),
        "system.packages",
    )
    plugin_id = "dart_flutter"
    settings_model = Settings
    package_folder = Path(__file__).parent
    executable = "/usr/bin/git"
    binaries: ClassVar[dict[str, str]] = {
        "flutter": "checkout/bin/flutter",
        "dart": "checkout/bin/dart",
    }
    manual: ClassVar[dict[StrEnum, str]] = {
        PluginAction.DOCTOR: (
            "Run flutter doctor to inspect optional platform SDKs. "
            "Review the suggested components and licenses."
        )
    }
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        **Toolchain.action_inputs,
        PluginAction.DOCTOR: Inputs,
    }

    def tool_binaries(self, settings: PluginSettings) -> dict[str, str]:
        return (
            self.binaries
            if Settings.model_validate(settings.model_dump()).publish_dart
            else {"flutter": "checkout/bin/flutter"}
        )

    def lock(self, stage: Path, settings: PluginSettings) -> bytes:
        ref = Reference.model_validate_json(
            read_https(
                "https://api.github.com/repos/flutter/flutter/git/ref/heads/stable",
                DownloadPolicy(frozenset({"api.github.com"}), 16384, 15, follow_redirects=False),
            )
        )
        return ref.object.sha.encode("ascii")

    def install_tools(self, stage: Path, folder: Path, lock: bytes) -> None:
        revision = lock.decode("ascii")
        if not re.fullmatch(r"[a-f0-9]{40}", revision):
            raise ValueError("Invalid Flutter commit")
        checkout = folder / "checkout"
        args = [
            "/usr/bin/git",
            "-c",
            "http.followRedirects=false",
            "-c",
            "core.hooksPath=/dev/null",
        ]
        execute([*args, "init", "--template=", str(checkout)], stage)
        execute([*args, "-C", str(checkout), "remote", "add", "origin", REPOSITORY], stage)
        execute(
            [*args, "-C", str(checkout), "fetch", "--depth=1", "--no-tags", "origin", revision],
            stage,
        )
        if (
            execute(
                [*args, "-C", str(checkout), "rev-parse", "FETCH_HEAD^{commit}"],
                stage,
                capture=True,
            ).strip()
            != revision
        ):
            raise ValueError("Fetched Flutter revision differs from the confirmed release")
        execute([*args, "-C", str(checkout), "checkout", "--detach", revision], stage)
        if (
            execute([*args, "-C", str(checkout), "rev-parse", "HEAD"], stage, capture=True).strip()
            != revision
        ):
            raise ValueError("Flutter checkout readback failed")
