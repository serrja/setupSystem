"""Exercise the exact isolated runtime inputs and its bounded wheel extraction."""

import hashlib
import stat
import tomllib
import zipfile
from pathlib import Path

import pytest

from setup_system.bootstrap.validation_runtime import (
    LockedWheel,
    locked_wheels,
    runtime_target,
    unpack,
)

ROOT = Path(__file__).resolve().parents[1]


def test_release_carries_exact_locked_system_runtime_for_each_target() -> None:
    directory = ROOT / "src/setup_system/resources/privileged_wheels"
    version = tomllib.loads((ROOT / "manifests/platforms.toml").read_text())["hyprland"][
        "system_python"
    ]
    files = set()
    for machine in ("x86_64", "aarch64", "amd64", "arm64"):
        observed_version, tag = runtime_target(ROOT, machine)
        assert observed_version == version
        wheels = locked_wheels(ROOT / "uv.lock", tag, version)
        for wheel in wheels:
            archive = directory / wheel.name
            with archive.open("rb") as stream:
                assert hashlib.file_digest(stream, "sha256").hexdigest() == wheel.digest
            assert archive.stat().st_size == wheel.size
            files.add(wheel.name)
    assert {path.name for path in directory.iterdir()} == files


@pytest.mark.parametrize(
    "member, mode",
    [
        ("../outside.py", stat.S_IFREG),
        ("startup.pth", stat.S_IFREG),
        ("pkg.data/scripts/run", stat.S_IFREG),
        ("pkg/link", stat.S_IFLNK),
    ],
)
def test_privileged_runtime_rejects_unsafe_members(tmp_path, member, mode):
    archive = tmp_path / "fixture.whl"
    with zipfile.ZipFile(archive, "w") as package:
        info = zipfile.ZipInfo(member)
        info.external_attr = (mode | 0o644) << 16
        package.writestr(info, b"fixture")
    output = tmp_path / "unpacked"
    output.mkdir()
    data = archive.read_bytes()
    wheel = LockedWheel(archive.name, "", hashlib.sha256(data).hexdigest(), len(data))
    with pytest.raises(ValueError, match="Unsafe member"):
        unpack(wheel, archive, output)
    assert not list(output.iterdir()) and not (tmp_path / "outside.py").exists()


def test_privileged_runtime_rejects_changed_bytes_and_overlapping_files(tmp_path):
    archive = tmp_path / "fixture.whl"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("shared.py", b"fixture")
    output = tmp_path / "unpacked"
    output.mkdir()
    data = archive.read_bytes()
    wheel = LockedWheel(archive.name, "", hashlib.sha256(data).hexdigest(), len(data))
    with pytest.raises(ValueError, match="lock"):
        unpack(wheel._replace(digest="0" * 64), archive, output)
    existing = output / "shared.py"
    existing.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        unpack(wheel, archive, output)
    assert existing.read_bytes() == b"original"
