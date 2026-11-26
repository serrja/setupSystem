"""Prepare Ruff and basedpyright from compatible hashed binary wheels."""

import re
import shutil
import sys
import tomllib
from importlib.resources import files as package_files
from pathlib import Path
from typing import ClassVar
from urllib.parse import unquote, urlsplit

from packaging.markers import Marker
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version
from setup_system_sdk.plugin import PluginSettings

from setup_plugin_python.models import Package, Settings
from setup_plugin_python.tools import Toolchain, execute


def uv() -> str:
    binary = shutil.which("uv") or next(
        (str(p) for p in (Path.home() / ".local/bin/uv", Path("/usr/local/bin/uv")) if p.is_file()),
        None,
    )
    if not binary:
        raise ValueError("The uv package manager is unavailable")
    return str(Path(binary).absolute())


def requirements(data: bytes) -> bytes:
    """Select one authenticated binary from each release selected by uv's resolver."""
    order = {tag: i for i, tag in enumerate(sys_tags())}
    lines = []

    for entry in tomllib.loads(data.decode())["packages"]:
        # Project and editable entries cannot participate in the native tools closure.
        if "directory" in entry or "vcs" in entry or any(k in entry for k in ("archive", "path")):
            raise ValueError("Unexpected local tool dependency")
        item = Package.model_validate(
            {key: entry[key] for key in Package.model_fields if key in entry}
        )
        if item.marker and not Marker(item.marker).evaluate():
            continue
        choices = []
        for artifact in item.wheels:
            link = urlsplit(artifact.url)
            filename = unquote(link.path).rsplit("/", 1)[-1]
            name, version, _, tags = parse_wheel_filename(filename)
            sha = artifact.hashes.get("sha256", "")
            if (
                link.scheme != "https"
                or link.netloc != "files.pythonhosted.org"
                or link.query
                or link.fragment
                or not re.fullmatch(
                    r"/packages/[a-f0-9]{2}/[a-f0-9]{2}/[a-f0-9]+/" + re.escape(filename), link.path
                )
                or name != canonicalize_name(item.name)
                or version != Version(item.version)
                or version.is_prerelease
                or not re.fullmatch(r"[0-9a-f]{64}", sha)
            ):
                raise ValueError("PyPI wheel identity does not match the resolved release")
            if compatible := [order[tag] for tag in tags if tag in order]:
                choices.append((min(compatible), artifact.url, sha))
        if not choices:
            raise ValueError("The release does not provide a compatible binary wheel")
        _, url, digest = min(choices)
        lines.append(f"{item.name} @ {url} --hash=sha256:{digest}")
    if not lines:
        raise ValueError("No verified tool wheels")
    return ("\n".join(sorted(lines)) + "\n").encode()


class PluginActions(Toolchain):
    plugin_id = "python"

    package_folder = Path(str(package_files("setup_plugin_python").joinpath("resources")))

    executable = "/usr/bin/python3"

    binaries: ClassVar[dict[str, str]] = {
        "ruff": "bin/ruff",
        "basedpyright": "bin/basedpyright",
        "basedpyright-langserver": "bin/basedpyright-langserver",
    }

    def lock(self, stage: Path, settings: PluginSettings) -> bytes:
        options = Settings.model_validate(settings.model_dump())
        specs = stage / "tools.in"
        specs.write_text("ruff\n" + ("basedpyright\n" if options.type_checker else ""))
        output = stage / "pylock.toml"
        execute(
            [
                uv(),
                "--no-config",
                "--no-cache",
                "pip",
                "compile",
                "--no-build",
                "--python",
                sys.executable,
                "--default-index",
                "https://pypi.org/simple",
                "--format",
                "pylock.toml",
                "--output-file",
                str(output),
                str(specs),
            ],
            stage,
        )
        if output.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Tool release metadata exceeds the limit")
        return requirements(output.read_bytes())

    def tool_binaries(self, settings: PluginSettings) -> dict[str, str]:
        return (
            self.binaries
            if Settings.model_validate(settings.model_dump()).type_checker
            else {"ruff": "bin/ruff"}
        )

    def install_tools(self, stage: Path, folder: Path, lock: bytes) -> None:
        requirements_file = stage / "requirements.txt"
        requirements_file.write_bytes(lock)
        # The generation already exists and is empty. uv writes its environment directly here.
        execute(
            [uv(), "--no-config", "--no-cache", "venv", "--python", sys.executable, str(folder)],
            stage,
        )
        execute(
            [
                uv(),
                "--no-config",
                "--no-cache",
                "pip",
                "install",
                "--no-build",
                "--no-index",
                "--require-hashes",
                "--python",
                str(folder / "bin/python"),
                "-r",
                str(requirements_file),
            ],
            stage,
        )
