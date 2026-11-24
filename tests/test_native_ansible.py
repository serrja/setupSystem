"""Execute real read-only plays and user staging with a private HOME."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def run_env(tmp_path):
    home = tmp_path / "user with spaces"
    home.mkdir(mode=0o700)
    work = tmp_path / "private run"
    work.mkdir(mode=0o700)
    request = work / "request.json"
    result = work / "result.json"
    request.write_text(json.dumps({"schema": 1, "settings": {}, "expected": None}))
    request.chmod(0o600)
    return dict(
        os.environ,
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / ".config"),
        XDG_DATA_HOME=str(home / ".local/share"),
        XDG_STATE_HOME=str(home / ".local/state"),
        XDG_CACHE_HOME=str(home / ".cache"),
        SETUP_SYSTEM_REQUEST=str(request),
        SETUP_SYSTEM_RESULT=str(result),
        ANSIBLE_CONFIG=str(ROOT / "ansible.cfg"),
        ANSIBLE_HOME=str(work / "ansible"),
        SETUP_SYSTEM_CANCEL=str(work / "cancel"),
        ANSIBLE_LOCAL_TEMP=str(work / "local"),
        ANSIBLE_REMOTE_TEMP=str(work / "remote"),
        ANSIBLE_FILTER_PLUGINS=str(ROOT / "ansible/filter_plugins"),
        ANSIBLE_NOCOLOR="true",
        PYTHONDONTWRITEBYTECODE="1",
    )


def play(args, env):
    path = Path(sys.executable).parent / "ansible-playbook"
    executable = str(path) if path.exists() else shutil.which("ansible-playbook")
    assert executable
    return subprocess.run(
        [executable, "-i", "localhost,", "-c", "local", *args],
        env=env,
        cwd=ROOT,
        umask=0o077,
        text=True,
        capture_output=True,
        timeout=90,
    )


def test_private_plan_never_changes_home_or_requests_become(run_env):
    task = play(["ansible/base.yml", "-e", "setup_action=plan"], run_env)
    assert task.returncode == 0, task.stdout + task.stderr
    result = json.loads(Path(run_env["SETUP_SYSTEM_RESULT"]).read_text())
    assert result["status"] in {"no_changes", "blocked", "nothing_selected"}, result
    assert not any(Path(run_env["HOME"]).iterdir())


def test_malformed_selections_fail_before_planning_or_mutation(run_env):
    path = Path(run_env["SETUP_SYSTEM_REQUEST"])
    path.write_text(json.dumps({"schema": 1, "settings": {"hyprland": "yes"}, "expected": None}))
    result = play(["ansible/base.yml", "-e", "setup_action=plan"], run_env)
    assert result.returncode == 0, result.stdout + result.stderr
    reply = json.loads(Path(run_env["SETUP_SYSTEM_RESULT"]).read_text())
    assert reply["status"] == "blocked" and reply["blockers"]
    assert not any(Path(run_env["HOME"]).iterdir())


def test_render_and_publish_desktop_with_real_unprivileged_ansible(run_env):
    import yaml

    stage = Path(run_env["SETUP_SYSTEM_REQUEST"]).parent
    home = Path(run_env["HOME"])
    intent = {
        "hyprland": True,
        "desktop_theme": True,
        "theme": {"rounding": 16, "effects": False, "compact_panel": True},
        "monitors": [],
        "apps": [],
        "languages": [],
    }
    info = {
        "home": str(home),
        "xdg": {
            key: run_env["XDG_" + key.upper() + "_HOME"]
            for key in ("data", "config", "state", "cache")
        },
    }
    fixture = stage / "theme.yml"
    tasks = [
        {"ansible.builtin.include_tasks": str(ROOT / "ansible/tasks/user_plan.yml")},
        {
            "ansible.builtin.copy": {
                "content": "{{ files_preview | to_json }}",
                "dest": str(stage / "preview.json"),
                "mode": "0600",
            }
        },
    ]
    variables = {
        "release_root": str(ROOT),
        "section": "base",
        "run_dir": str(stage),
        "facts": info,
        "intent": intent,
        "latest_tools": {},
        "user_changes": [],
    }
    fixture.write_text(
        yaml.safe_dump(
            [
                {
                    "hosts": "localhost",
                    "gather_facts": False,
                    "become": False,
                    "vars": variables,
                    "tasks": tasks,
                }
            ]
        )
    )
    task = play([str(fixture)], run_env)
    assert task.returncode == 0, task.stdout + task.stderr
    preview = json.loads((stage / "preview.json").read_text())
    assert ".config/hypr/hyprland.lua" in preview["changes"]
    profile = stage / "files/.config/hypr/setup-system/profile.lua"
    assert "rounding = 16" in profile.read_text() and "@{" not in profile.read_text()
    assert (
        json.loads((stage / "files/.config/hypr/setup-system/waybar.json").read_text())["height"]
        == 30
    )
    assert not any(home.iterdir())
    payload = {"schema": 1, "settings": intent, "expected": {"observed": {"files": preview}}}
    Path(run_env["SETUP_SYSTEM_REQUEST"]).write_text(json.dumps(payload))
    variables.update(
        files_preview=preview,
        selected=[],
        commands={},
        changed_stages=[],
        tool_prefix=str(Path(info["xdg"]["data"]) / "setup-system/tools"),
        user_changes=preview["changes"],
    )
    fixture.write_text(
        yaml.safe_dump(
            [
                {
                    "hosts": "localhost",
                    "gather_facts": False,
                    "become": False,
                    "vars": variables,
                    "tasks": [
                        {
                            "ansible.builtin.include_tasks": str(
                                ROOT / "ansible/tasks/user_apply.yml"
                            )
                        }
                    ],
                }
            ]
        )
    )
    result = play([str(fixture)], run_env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / ".config/hypr/setup-system/profile.lua").read_bytes() == profile.read_bytes()
    assert os.access(home / ".local/share/setup-system/bin/start-hyprland", os.X_OK)


def test_cancellation_boundary_stops_following_mutation(run_env):
    import yaml

    stage = Path(run_env["SETUP_SYSTEM_REQUEST"]).parent
    marker = Path(run_env["SETUP_SYSTEM_CANCEL"])
    next_file = stage / "should-not-run"
    result = stage / "completed"
    fixture = stage / "cancel.yml"
    boundary = str(ROOT / "ansible/tasks/cancel.yml")
    tasks = [
        {
            "name": "Record first completed fixture stage",
            "ansible.builtin.set_fact": {"changed_stages": ["APT fixture"]},
        },
        {
            "name": "Request cancellation",
            "ansible.builtin.copy": {"content": "", "dest": str(marker), "mode": "0600"},
        },
        {
            "block": [
                {"ansible.builtin.include_tasks": boundary},
                {
                    "ansible.builtin.copy": {
                        "content": "unexpected",
                        "dest": str(next_file),
                        "mode": "0600",
                    }
                },
            ],
            "rescue": [
                {
                    "ansible.builtin.copy": {
                        "content": "{{ [cancel_requested, changed_stages] | to_json }}",
                        "dest": str(result),
                        "mode": "0600",
                    }
                }
            ],
        },
    ]
    fixture.write_text(
        yaml.safe_dump(
            [{"hosts": "localhost", "gather_facts": False, "become": False, "tasks": tasks}]
        )
    )
    run = play([str(fixture)], run_env)
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads(result.read_text()) == [True, ["APT fixture"]]
    assert not next_file.exists()
