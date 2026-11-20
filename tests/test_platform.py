"""Safety gates for the installer's explicit platform boundary."""

import tomllib
from pathlib import Path

import pytest

from setup_system.capabilities import parse_policy
from setup_system.planning import detect_platform


@pytest.mark.parametrize(
    ("distribution", "version", "architecture", "implemented"),
    [
        ("debian", "12", "x86_64", True),
        ("debian", "13", "aarch64", True),
        ("ubuntu", "24.04", "x86_64", False),
        ("ubuntu", "26.04", "x86_64", False),
        ("linuxmint", "22", "x86_64", False),
        ("fedora", "43", "x86_64", False),
        ("debian", "11", "x86_64", False),
    ],
)
def test_explicit_linux_platform(
    tmp_path: Path, distribution: str, version: str, architecture: str, implemented: bool
) -> None:
    release = tmp_path / "os-release"
    release.write_text(f"ID={distribution}\nVERSION_ID={version}\nID_LIKE=debian\n")
    platform_adapter = detect_platform(release, architecture=architecture, system_name="Linux")
    assert platform_adapter.implemented is implemented
    if architecture == "aarch64":
        assert platform_adapter.architecture == "arm64"


def test_macos_is_unsupported() -> None:
    assert not detect_platform(system_name="Darwin", architecture="arm64").implemented


@pytest.mark.parametrize("architecture", ["x86_64", "amd64", "arm64", "aarch64"])
@pytest.mark.parametrize("version", ["12", "13"])
def test_all_manifest_targets(tmp_path: Path, architecture: str, version: str) -> None:
    release = tmp_path / "os-release"
    release.write_text(f"ID=debian\nVERSION_ID={version}\n")
    assert detect_platform(release, architecture=architecture, system_name="Linux").implemented


def test_unknown_architecture_and_release_are_rejected(tmp_path: Path) -> None:
    release = tmp_path / "os-release"
    release.write_text("ID=debian\nVERSION_ID=13\n")
    assert not detect_platform(release, architecture="riscv64", system_name="Linux").implemented
    release.write_text("ID=debian\nVERSION_ID=99\n")
    assert not detect_platform(release, architecture="x86_64", system_name="Linux").implemented


def test_platform_support_is_loaded_from_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from setup_system import planning

    manifest = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "manifests/platforms.toml").read_text()
    )
    manifest["debian"]["versions"] = ["88"]
    manifest["debian"]["codenames"]["88"] = "fixture"
    manifest["hyprland"].update(version="88", codename="fixture", backports="fixture-backports")
    policy = parse_policy(manifest)
    monkeypatch.setattr(planning, "load_policy", lambda: policy)
    release = tmp_path / "os-release"
    release.write_text("ID=debian\nVERSION_ID=88\n")
    assert planning.detect_platform(release, architecture="amd64", system_name="Linux").implemented
    release.write_text("ID=debian\nVERSION_ID=13\n")
    assert not planning.detect_platform(
        release, architecture="amd64", system_name="Linux"
    ).implemented
    assert list(tmp_path.iterdir()) == [release]
