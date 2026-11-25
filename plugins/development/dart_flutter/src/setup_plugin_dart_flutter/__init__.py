"""Clone the official stable Flutter commit without touching existing SDKs or projects."""

import re
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field

from setup_system.infrastructure.downloads import DownloadPolicy, read_https
from setup_system_sdk import Inputs, Plan, PluginSettings
from setup_system_sdk.apt import apply_packages
from setup_system_sdk.toolchain import Toolchain, execute


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


class Settings(PluginSettings):
    publish_dart: bool = Field(
        default=True,
        title="Publish Dart command",
        description="Expose the Dart SDK delivered by this Flutter release.",
    )


class Plugin(Toolchain):
    plugin_id = "dart_flutter"
    settings_model = Settings
    package_folder = Path(__file__).parent
    executable = "/usr/bin/git"
    binaries: ClassVar[dict[str, str]] = {
        "flutter": "checkout/bin/flutter",
        "dart": "checkout/bin/dart",
    }
    manual: ClassVar[dict[str, str]] = {
        PluginAction.DOCTOR: (
            "Run flutter doctor to inspect optional platform SDKs. "
            "Review the suggested components and licenses."
        )
    }
    inputs: ClassVar[dict[str, type[Inputs]]] = {**Toolchain.inputs, PluginAction.DOCTOR: Inputs}

    def commands(self, settings: PluginSettings) -> dict[str, str]:
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


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    apply_packages("dart_flutter", action_id, plan)
