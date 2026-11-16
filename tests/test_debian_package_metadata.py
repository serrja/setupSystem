"""Session-free package inspection must distinguish absent, broken and installed state."""

from __future__ import annotations

import subprocess

import pytest

from setup_system.adapters.debian.packages import installed_version


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        ("install ok installed\t0.55.2+ds-1~bpo13+1\n", 0, "0.55.2+ds-1~bpo13+1"),
        ("deinstall ok config-files\t0.55.2\n", 0, None),
        ("install ok unpacked\t0.55.2\n", 0, None),
        ("", 1, None),
    ],
)
def test_inspect_installed_state(
    monkeypatch: pytest.MonkeyPatch, status: str, code: int, expected: str | None
) -> None:
    def execute(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert argv[0] == "/usr/bin/dpkg-query" and argv[-1] == "hyprland"
        assert kwargs["check"] is False and kwargs["timeout"] == 15
        return subprocess.CompletedProcess(argv, code, status, "")

    monkeypatch.setattr(subprocess, "run", execute)
    assert installed_version("hyprland") == expected


def test_query_failure_is_not_an_absent_package(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 2, "", "failed")
    )
    with pytest.raises(subprocess.CalledProcessError):
        installed_version("hyprland")


@pytest.mark.parametrize("package", ["--help", "../hyprland", "hyprland;true"])
def test_unsafe_query_fails_before_process(monkeypatch: pytest.MonkeyPatch, package: str) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("Unsafe query"))
    with pytest.raises(ValueError, match="package name"):
        installed_version(package)
