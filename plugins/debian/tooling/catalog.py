"""Build the offline catalog for CPython on the supported workstation architectures."""

import hashlib
import tomllib
from importlib.metadata import distributions
from pathlib import Path
from typing import Annotated

import anyio
import typer
from packaging.markers import Environment, default_environment
from packaging.tags import Tag, compatible_tags, cpython_tags
from packaging.utils import parse_wheel_filename
from setup_system_sdk import PLUGIN_GROUP
from setup_system_sdk.downloads import GITHUB_RELEASE_HOSTS, DownloadPolicy, download_https
from setup_system_sdk.manifests import Manifest

from docker.plugin_artifacts import descriptor, generate, locked_closure
from setup_system.infrastructure.plugins.uv_runtime import ARCHIVES, LIMIT, VERSION, _unpack


def target(arch: str) -> tuple[tuple[Tag, ...], Environment]:
    platforms = [f"manylinux_2_{minor}_{arch}" for minor in range(41, 16, -1)]
    platforms.extend([f"manylinux2014_{arch}", f"linux_{arch}"])
    tags = (
        *cpython_tags((3, 13), ["cp313"], platforms),
        *compatible_tags((3, 13), "cp313", platforms),
    )
    markers = default_environment()
    markers["platform_machine"] = arch
    markers["platform_system"] = "Linux"
    markers["sys_platform"] = "linux"
    markers["python_version"] = "3.13"
    markers["python_full_version"] = "3.13.0"
    markers["implementation_version"] = "3.13.0"
    return tags, markers


def requirements(path: Path, wheels: list[dict[str, str]]) -> None:
    selected: dict[str, tuple[str, str]] = {}
    for wheel in wheels:
        name, version, _, _ = parse_wheel_filename(wheel["name"])
        record = (str(version), wheel["sha256"])
        if name in selected and selected[name] != record:
            raise ValueError(f"Conflicting locked runtime distribution {name}")
        selected[name] = record
    path.write_text(
        "".join(
            f"{name}=={version} --hash=sha256:{digest}\n"
            for name, (version, digest) in sorted(selected.items())
        )
    )


async def verified_uv(output: Path, arch: str) -> None:
    name = f"uv-{arch}-unknown-linux-gnu"
    url = f"https://github.com/astral-sh/uv/releases/download/{VERSION}/{name}.tar.gz"
    content = await download_https(url, DownloadPolicy(GITHUB_RELEASE_HOSTS, LIMIT, 30, 180))
    folder = output / "uv" / ("amd64" if arch == "x86_64" else "arm64")
    folder.mkdir(parents=True, exist_ok=True)
    binary = Path(_unpack(content, name, ARCHIVES[arch], folder))
    with binary.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (folder / "uv.sha256").write_text(f"{digest}  uv\n")


async def assemble(wheels: Path, host_wheel: Path, output: Path, lock: Path) -> None:
    (sdk,) = wheels.glob("setup_system_sdk-*.whl")
    handlers = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "packaging/executors.toml").read_text()
    )["handlers"]
    native_wheels = [
        wheel
        for wheel in sorted(wheels.glob("*.whl"))
        if parse_wheel_filename(wheel.name)[0]
        in {f"setup-system-plugin-{plugin}" for plugin in handlers}
    ]
    if len(native_wheels) != len(handlers):
        raise ValueError("Every native handler needs one locked primary wheel")
    for primary in sorted(wheels.glob("*.whl")):
        (distribution,) = distributions(path=[str(primary)])
        if not distribution.entry_points.select(group=PLUGIN_GROUP):
            continue
        records = []
        for arch in ("x86_64", "aarch64"):
            tags, markers = target(arch)
            record = await generate(
                primary, sdk, lock, output, target_tags=tags, environment=markers
            )
            if record not in records:
                records.append(record)
        manifest = Manifest.model_validate(records[0]["manifest"])
        descriptor(output / (manifest.id + ".json"), records)
    for arch in ("x86_64", "aarch64"):
        deb_arch = "amd64" if arch == "x86_64" else "arm64"
        tags, markers = target(arch)
        directory = output / "runtime" / "wheelhouse" / deb_arch
        host = await locked_closure(
            host_wheel, sdk, lock, directory, target_tags=tags, environment=markers
        )
        requirements(output / "runtime" / f"host-{deb_arch}.txt", host)
        native: list[dict[str, str]] = []
        for primary in native_wheels:
            native.extend(
                await locked_closure(
                    primary, sdk, lock, directory, target_tags=tags, environment=markers
                )
            )
        requirements(output / "runtime" / f"native-{deb_arch}.txt", native)
        await verified_uv(output / "runtime", arch)


def main(
    wheels: Annotated[Path, typer.Option("--wheels")],
    host_wheel: Annotated[Path, typer.Option("--host-wheel")],
    output: Annotated[Path, typer.Option("--output")],
    lock: Annotated[Path, typer.Option("--lock")] = Path("uv.lock"),
) -> None:
    """Build both architecture catalogs from the workspace lock."""
    anyio.run(assemble, wheels, host_wheel, output, lock)


if __name__ == "__main__":
    typer.run(main)
