"""Resolve a script-free npm installation of TypeScript, Vue and Prettier."""

import base64
import binascii
import json
import re
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, JsonValue
from setup_system_sdk import PluginSettings
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_toolchain.tools import Toolchain
from setup_system_toolchain.tools import execute as execute_tool


def execute(argv: list[str], stage: Path, *, capture: bool = False) -> str:
    return execute_tool(
        argv,
        stage,
        capture=capture,
        environment={
            "NPM_CONFIG_USERCONFIG": str(stage / "npm-user.rc"),
            "NPM_CONFIG_GLOBALCONFIG": str(stage / "npm-global.rc"),
            "NPM_CONFIG_ENGINE_STRICT": "true",
            "NPM_CONFIG_CACHE": str(stage / "cache"),
        },
    )


class Settings(PluginSettings):
    prettier: bool = Field(
        default=True,
        title="Install Prettier",
        description="Publish the formatter with the Vue and TypeScript language servers.",
    )


class NpmPackage(BaseModel):
    name: str | None = None
    version: str
    resolved: str
    integrity: str
    link: bool = False


def checked_lock(data: bytes) -> bytes:
    value: dict[str, JsonValue] = json.loads(data)
    packages = value.get("packages")
    if value.get("lockfileVersion") != 3 or not isinstance(packages, dict) or not packages.get(""):
        raise ValueError("Expected a current npm dependency lock")
    for path, raw in packages.items():
        if not path:
            continue
        item = NpmPackage.model_validate(raw)
        parts = Path(path).parts
        url = urlsplit(item.resolved)
        installed_name = path.rsplit("node_modules/", 1)[-1]
        package = item.name or installed_name
        archive = f"/{package}/-/{package.rsplit('/', 1)[-1]}-{item.version}.tgz"
        method, _, encoded = item.integrity.partition("-")
        try:
            digest = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Invalid npm integrity value") from exc
        if (
            not parts
            or parts[0] != "node_modules"
            or ".." in parts
            or item.link
            or not re.fullmatch(r"(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+", package)
            or not re.fullmatch(r"[0-9]+[.][0-9]+[.][0-9]+(?:[-+][0-9A-Za-z.-]+)?", item.version)
            or url.path != archive
            or url.scheme != "https"
            or url.netloc != "registry.npmjs.org"
            or url.query
            or url.fragment
            or not re.fullmatch(
                r"/(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+/-/[A-Za-z0-9_+.-]+\.tgz", url.path
            )
            or method != "sha512"
            or len(digest) != 64
        ):
            raise ValueError("Unexpected npm dependency source or integrity")
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class Plugin(Toolchain):
    delegated_step_key = "packages"
    delegated_actions: ClassVar[dict[str, str]] = dict.fromkeys(
        (LifecycleAction.INSTALL, LifecycleAction.UPDATE, LifecycleAction.UNINSTALL),
        "system.packages",
    )
    plugin_id = "typescript_vue"
    package_folder = Path(__file__).parent
    executable = "/usr/bin/node"
    settings_model = Settings
    binaries: ClassVar[dict[str, str]] = {
        name: "node_modules/.bin/" + name
        for name in ("tsc", "typescript-language-server", "vue-language-server", "prettier")
    }

    def lock(self, stage: Path, settings: PluginSettings) -> bytes:
        options = Settings.model_validate(settings.model_dump())
        deps = {
            "typescript": "^6",
            "typescript-language-server": "^5",
            "@vue/language-server": "latest",
            "@vue/typescript-plugin": "latest",
        }
        if options.prettier:
            deps["prettier"] = "latest"
        (stage / "package.json").write_text(
            json.dumps(
                {
                    "name": "setup-system-typescript",
                    "version": "1.0.0",
                    "private": True,
                    "dependencies": deps,
                },
                sort_keys=True,
            )
        )
        execute(
            [
                "/usr/bin/npm",
                "install",
                "--package-lock-only",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                "--registry=https://registry.npmjs.org",
                "--prefix",
                str(stage),
            ],
            stage,
        )
        lock = stage / "package-lock.json"
        if lock.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("Tool dependency lock exceeds the limit")
        return checked_lock(lock.read_bytes())

    def tool_binaries(self, settings: PluginSettings) -> dict[str, str]:
        return {
            k: v
            for k, v in self.binaries.items()
            if k != "prettier" or Settings.model_validate(settings.model_dump()).prettier
        }

    def links(self, folder: Path, settings: PluginSettings) -> dict[Path, bytes]:
        result = super().links(folder, settings)
        result[self.storage() / "modules.txt"] = (str(folder / "node_modules") + "\n").encode()
        return result

    def install_tools(self, stage: Path, folder: Path, lock: bytes) -> None:
        root = json.loads(lock)["packages"][""]
        (folder / "package.json").write_text(
            json.dumps(
                {
                    "name": root["name"],
                    "version": root["version"],
                    "private": True,
                    "dependencies": root["dependencies"],
                }
            )
        )
        (folder / "package-lock.json").write_bytes(lock)
        execute(
            [
                "/usr/bin/npm",
                "ci",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                "--registry=https://registry.npmjs.org",
                "--prefix",
                str(folder),
            ],
            stage,
        )
