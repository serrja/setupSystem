"""Safety gates for the installer's explicit platform boundary."""

from pathlib import Path

import pytest

from setup_system.infrastructure import native_target

ROOT = Path(__file__).resolve().parents[1]
DEBIAN = {"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"}


@pytest.mark.parametrize(
    ("distribution", "version", "architecture", "implemented"),
    [
        ("debian", "12", "x86_64", False),
        ("debian", "13", "aarch64", True),
        ("ubuntu", "24.04", "x86_64", False),
        ("ubuntu", "26.04", "x86_64", False),
        ("linuxmint", "22", "x86_64", False),
        ("fedora", "43", "x86_64", False),
        ("debian", "11", "x86_64", False),
    ],
)
def test_explicit_linux_platform(tmp_path, distribution, version, architecture, implemented):
    release = dict(DEBIAN, ID=distribution, VERSION_ID=version, ID_LIKE="debian")
    arch, supported = native_target.target(
        release, architecture, native_target.architecture(architecture)
    )
    assert supported is implemented
    if architecture == "aarch64":
        assert arch == "arm64"
    assert not list(tmp_path.iterdir())


def test_macos_is_unsupported(monkeypatch):
    def missing_linux_release():
        raise OSError("No freedesktop os-release on Darwin")

    monkeypatch.setattr(native_target.platform, "freedesktop_os_release", missing_linux_release)
    monkeypatch.setattr(native_target.platform, "machine", lambda: "arm64")
    assert native_target.current_target() == ("arm64", False)


@pytest.mark.parametrize("architecture", ["x86_64", "amd64", "arm64", "aarch64"])
@pytest.mark.parametrize(
    ("version", "codename", "supported"),
    [("13", "trixie", True), ("12", "bookworm", False), ("13", "bookworm", False)],
)
def test_all_manifest_targets(architecture, version, codename, supported):
    release = dict(DEBIAN, VERSION_ID=version, VERSION_CODENAME=codename)
    assert (
        native_target.target(release, architecture, native_target.architecture(architecture))[1]
        is supported
    )


def test_unknown_architecture_and_release_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(native_target.platform, "freedesktop_os_release", lambda: DEBIAN)
    monkeypatch.setattr(native_target.platform, "machine", lambda: "riscv64")
    assert native_target.current_target() == ("riscv64", False)
    monkeypatch.setattr(native_target.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        native_target.platform, "freedesktop_os_release", lambda: dict(DEBIAN, VERSION_ID="99")
    )
    assert native_target.current_target() == ("amd64", False)
    assert not list(tmp_path.iterdir())
