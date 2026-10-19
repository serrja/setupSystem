"""Safety gates for the installer's explicit platform boundary."""

from pathlib import Path

import pytest

from setup_system.adapters.debian.docker import install_docker
from setup_system.application.happ import classify_link
from setup_system.planning import Platform, create_plan, detect_platform


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


def test_plan_blocks_unpinned_linux_arm64_editor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "setup_system.planning.detect_platform",
        lambda: Platform("debian", "13", "arm64", True, False, "explicit Debian"),
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    result = create_plan(tmp_path, "terminalDev")
    assert result.blocked
    assert result.steps == ()
    assert list(tmp_path.iterdir()) == []


def test_bad_link_error_never_repeats_secret() -> None:
    secret = "bad-secret\n"
    with pytest.raises(ValueError) as exc:
        classify_link(secret)
    assert secret.strip() not in str(exc.value)
    assert classify_link("happ://routing/add/YQ==", routing_profile=True) == "routingAdd"


def test_docker_provider_stops_before_external_actions_on_unsupported_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "setup_system.adapters.debian.docker.detect_platform",
        lambda: Platform("ubuntu", "26.04", "x86_64", False, False, "UNSUPPORTED"),
    )
    monkeypatch.setattr(
        "setup_system.adapters.debian.docker.subprocess.run",
        lambda *args, **kwargs: pytest.fail("subprocess ran before platform gate"),
    )
    monkeypatch.setattr(
        "setup_system.adapters.debian.docker.urlopen",
        lambda *args, **kwargs: pytest.fail("network ran before platform gate"),
    )
    with pytest.raises(ValueError, match="UNSUPPORTED"):
        install_docker()
