"""Build the offline catalog for CPython on the supported workstation architectures."""

import argparse
from pathlib import Path

import anyio
from packaging.markers import default_environment
from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import parse_wheel_filename
from plugin_artifacts import descriptor, generate
from setup_system_sdk.manifests import Manifest


async def assemble(wheels: Path, output: Path, lock: Path) -> None:
    (sdk,) = wheels.glob("setup_system_sdk-*.whl")
    for primary in sorted(wheels.glob("*.whl")):
        if parse_wheel_filename(primary.name)[0] in {"setup-system", "setup-system-sdk"}:
            continue
        records = []
        for arch in ("x86_64", "aarch64"):
            platforms = [f"manylinux_2_{minor}_{arch}" for minor in range(41, 16, -1)]
            platforms.extend([f"manylinux2014_{arch}", f"linux_{arch}"])
            tags = (
                *cpython_tags((3, 13), ["cp313"], platforms),
                *compatible_tags((3, 13), "cp313", platforms),
            )
            markers = default_environment()
            markers.update(
                platform_machine=arch,
                platform_system="Linux",
                sys_platform="linux",
                python_version="3.13",
                python_full_version="3.13.0",
                implementation_version="3.13.0",
            )
            record = await generate(
                primary, sdk, lock, output, target_tags=tags, environment=markers
            )
            if record not in records:
                records.append(record)
        manifest = Manifest.model_validate(records[0]["manifest"])
        descriptor(output / (manifest.id + ".json"), records)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lock", type=Path, default=Path("uv.lock"))
    args = parser.parse_args()
    anyio.run(assemble, args.wheels, args.output, args.lock)
