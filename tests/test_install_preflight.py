"""Unsafe managed descendants must fail before system apply, even without Python."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from setup_system import installation as install
from setup_system import planning
from setup_system.application import workstation
from setup_system.application.installer import InstallationService, InstallRequest, Operation
from setup_system.runtime import UserRuntime

ROOT = Path(__file__).resolve().parents[1]
UNSAFE_DESTINATIONS = [
    ".local/share/setup-system/python",
    ".cache/setup-system/uv",
    ".config/hypr",
    ".config/hypr/setup-system/profile.lua",
]


def unsafe_home(tmp_path: Path, relative: str) -> tuple[Path, Path]:
    home = tmp_path / "home with spaces"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("owner data")
    destination = home / relative
    destination.parent.mkdir(parents=True)
    destination.symlink_to(outside, target_is_directory=True)
    return home, outside


@pytest.mark.parametrize("relative", UNSAFE_DESTINATIONS)
def test_nested_user_symlink_stops_root_orchestration_before_apt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    home, outside = unsafe_home(tmp_path, relative)
    target = install.TargetUser("alice", os.getuid(), os.getgid(), home, "/bin/bash")
    monkeypatch.setattr(install, "resolve_target", lambda name: target)
    monkeypatch.setattr(install, "current_packages", lambda root: ["hyprland=pin"])
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "getgrouplist", lambda name, gid: [gid])
    monkeypatch.setattr(
        planning,
        "detect_platform",
        lambda: planning.Platform("debian", "13", "x86_64", True, False, ""),
    )
    system_calls: list[str] = []
    monkeypatch.setattr(install, "prepare_packages", lambda *a, **kw: system_calls.append("APT"))
    monkeypatch.setattr(install, "mark_user_stage", lambda *a, **kw: None)

    def user_executor(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        # Run the actual later validator; this executor never installs packages or downloads.
        with monkeypatch.context() as child:
            child.setattr(os, "geteuid", lambda: target.uid)
            for key, value in install.user_environment(target, {}).items():
                child.setenv(key, value)
            try:
                if relative.startswith(".config/"):
                    workstation._targets(ROOT, "hyprland")
                else:
                    UserRuntime(ROOT).prepare()
            except ValueError as error:
                assert "symlink" in str(error)
                return subprocess.CompletedProcess(argv, 5, "", str(error))
        pytest.fail("The real user-stage validator accepted the unsafe path")

    monkeypatch.setattr(install, "run_command", user_executor)
    code = InstallationService(ROOT).run(InstallRequest(Operation.APPLY, user="alice", yes=True))
    assert system_calls == [], "Unsafe user paths were discovered only after root APT apply"
    assert code == 2
    assert (outside / "keep.txt").read_text() == "owner data"
