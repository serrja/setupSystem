"""Installed Ansible resources delimit the system privilege boundary."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "section,action", [("base", "apply"), ("workspace", "apply"), ("workspace", "cleanup-apply")]
)
def test_system_apply_requires_the_installed_package(tmp_path, section, action):
    # A checkout can only preview system changes. A user-controlled interpreter
    # or Ansible config must never be selected for privileged tasks.
    home = tmp_path / "home"
    home.mkdir()
    marker = home / "launched"
    command = tmp_path / "ansible-playbook"
    command.write_text("#!/bin/sh\ntouch " + str(marker) + "\n")
    command.chmod(0o700)
    reply = subprocess.run(
        ["/bin/sh", ROOT / "libexec/play.sh", section, action],
        cwd=tmp_path,
        env={
            "PATH": str(tmp_path),
            "HOME": str(home),
            "SETUP_SYSTEM_INTERPRETER": str(command),
            "ANSIBLE_CONFIG": str(tmp_path / "config"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert reply.returncode == 2 and "Install the Debian package" in reply.stderr
    assert list(home.iterdir()) == []


@pytest.mark.parametrize(
    "variant", ["shared_stage", "shared_request", "symlink_request", "unexpected_result"]
)
def test_play_rejects_an_unsafe_request_before_selecting_any_executable(tmp_path, variant):
    stage = tmp_path / "run"
    stage.mkdir(mode=0o700)
    request = stage / "data.json"
    request.write_bytes(b"{}")
    request.chmod(0o600)
    result = stage / "result.json"
    if variant == "shared_stage":
        stage.chmod(0o750)
    elif variant == "shared_request":
        request.chmod(0o640)
    elif variant == "symlink_request":
        link = stage / "request-link"
        link.symlink_to(request)
        request = link
    else:
        result = tmp_path / "result.json"
    marker = tmp_path / "invoked"
    fake = tmp_path / "python"
    fake.write_text("#!/bin/sh\ntouch " + str(marker) + "\n")
    fake.chmod(0o700)
    (tmp_path / "ansible-playbook").touch()
    response = subprocess.run(
        ["/bin/sh", ROOT / "libexec/play.sh", "base", "plan"],
        cwd=tmp_path,
        env=dict(
            os.environ,
            SETUP_SYSTEM_INTERPRETER=str(fake),
            SETUP_SYSTEM_REQUEST=str(request),
            SETUP_SYSTEM_RESULT=str(result),
        ),
        capture_output=True,
        check=False,
    )
    assert response.returncode == 2 and not marker.exists() and not result.exists()
