"""Safety gates for the installer's explicit platform boundary."""

from pathlib import Path

import pytest

from setup_system.adapters.debian.docker import installDocker
from setup_system.application.happ import classifyLink
from setup_system.planning import Platform, createPlan, detectPlatform


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
    platformAdapter = detectPlatform(release, architecture=architecture, systemName="Linux")
    assert platformAdapter.implemented is implemented
    if architecture == "aarch64":
        assert platformAdapter.architecture == "arm64"


def test_macos_is_unsupported() -> None:
    assert not detectPlatform(systemName="Darwin", architecture="arm64").implemented


def test_plan_blocks_unpinned_linux_arm64_editor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "setup_system.planning.detectPlatform",
        lambda: Platform("debian", "13", "arm64", True, False, "explicit Debian"),
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    result = createPlan(tmp_path, "terminalDev")
    assert result.blocked
    assert result.steps == ()
    assert list(tmp_path.iterdir()) == []


def test_bad_link_error_never_repeats_secret() -> None:
    secret = "bad-secret\n"
    with pytest.raises(ValueError) as exc:
        classifyLink(secret)
    assert secret.strip() not in str(exc.value)
    assert classifyLink("happ://routing/add/YQ==", routingProfile=True) == "routingAdd"


def test_docker_provider_stops_before_external_actions_on_unsupported_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "setup_system.adapters.debian.docker.detectPlatform",
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
        installDocker()
