"""Unsafe managed descendants must fail before system apply, even without Python."""

from __future__ import annotations

import importlib.util
import os
import shlex
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

from setup_system import installation as install
from setup_system import planning
from setup_system.application import workstation

ROOT = Path(__file__).resolve().parents[1]
UNSAFE_DESTINATIONS = [
    ".local/share/setup-system/python",
    ".cache/setup-system/uv",
    ".config/hypr",
    ".config/hypr/setup-system/profile.lua",
]


def bootstrap() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "setup_preflight_bootstrap", ROOT / "bootstrap.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    boot = bootstrap()
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
    monkeypatch.setattr(install, "install_system", lambda *a, **kw: system_calls.append("APT"))
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
                    boot._user_runtime()
            except ValueError as error:
                assert "symlink" in str(error)
                return subprocess.CompletedProcess(argv, 5, "", str(error))
        pytest.fail("The real user-stage validator accepted the unsafe path")

    monkeypatch.setattr(install, "run_command", user_executor)
    code = boot.main(["apply", "--yes", "--user", "alice"])
    assert system_calls == [], "Unsafe user paths were discovered only after root APT apply"
    assert code == 2
    assert (outside / "keep.txt").read_text() == "owner data"


@pytest.mark.parametrize("relative", UNSAFE_DESTINATIONS)
def test_nested_symlink_stops_no_python_shell_before_prerequisites(
    tmp_path: Path, relative: str
) -> None:
    home, outside = unsafe_home(tmp_path, relative)
    log = tmp_path / "mutations"
    uid, gid = os.getuid(), os.getgid()
    script = f"""
source {shlex.quote(str(ROOT / "setup.sh"))}
fixture_home={shlex.quote(str(home))}
fixture_log={shlex.quote(str(log))}
setup_platform() {{ return 0; }}
setup_source_access() {{ return 0; }}
id() {{
    case "$*" in '-u') printf 0;; '-u alice') printf {uid};;
        '-g alice') printf {gid};; '-un') printf alice;; *) return 2;; esac
}}
getent() {{
    case "$1:$2" in
        passwd:alice) printf 'alice:x:%s:%s::%s:/bin/bash\\n' {uid} {gid} "$fixture_home";;
        group:{gid}) printf 'fixture:x:%s:\\n' {gid};; *) return 2;;
    esac
}}
command() {{
    if [[ $1 == -v && $2 == python3 ]]; then return 1; fi
    builtin command "$@"
}}
setup_python_prerequisite() {{ printf 'APT\\n' >> "$fixture_log"; }}
exec() {{ printf 'user-stage\\n' >> "$fixture_log"; exit 0; }}
setup_main --yes
"""
    result = subprocess.run(
        ["/usr/bin/bash", "-c", script],
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": "/root",
            "SUDO_USER": "alice",
            "SUDO_UID": str(uid),
            "SUDO_GID": str(gid),
        },
        input="",
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    calls = log.read_text() if log.exists() else ""
    assert calls == "", f"Unsafe nested path reached a mutating shell stage: {calls}"
    assert result.returncode == 2, result.stdout + result.stderr
    assert (outside / "keep.txt").read_text() == "owner data"
