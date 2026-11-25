"""One immutable plan, one mutation, truthful partial results and safe cancellation."""

import asyncio
import io
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from test_native_ui import Tasks, app_for, idle
from textual.widgets import Button, Input, Static

from setup_system.models import PlanSnapshot, WorkspaceOptions
from setup_system.runner import AsyncMakeRunner, Command, Target
from setup_system.ui.dialogs import Password

ROOT = Path(__file__).resolve().parents[1]


def system_task(monkeypatch, action, data, **fixtures):
    scope = runpy.run_path(str(ROOT / "ansible/files/system_contract.py"))
    command = scope["run"]
    for key, value in fixtures.items():
        monkeypatch.setitem(command.__globals__, key, value)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data)))
    return command(scope["SystemTask"](action))


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory):
    """Execute the production Ansible status and failure tasks with recorded stage inputs."""
    run = tmp_path_factory.mktemp("native outcomes")
    definitions = yaml.safe_load((ROOT / "ansible/tasks/run.yml").read_text())[1]
    success = definitions["block"]
    selection = next(
        task for task in success if "has_selection" in task.get("ansible.builtin.set_fact", {})
    )
    plan = next(task for task in success if "plan" in task.get("ansible.builtin.set_fact", {}))
    report = next(task for task in success if "outcome" in task.get("ansible.builtin.set_fact", {}))
    failure = definitions["rescue"][0]
    scenarios = {
        "empty": ({}, False, False, [], [], False),
        "matched": ({"apps": ["firefox"]}, False, False, [], [], False),
        "failed": ({"apps": ["firefox"]}, False, False, [], [], True),
        "partial": (
            {"apps": ["firefox"]},
            True,
            False,
            [{"kind": "packages_verified", "label": "Packages"}],
            [],
            True,
        ),
        "cancel": (
            {"apps": ["firefox"]},
            True,
            True,
            [{"kind": "packages_verified", "label": "Packages"}],
            [],
            True,
        ),
        "cancel_before": ({"apps": ["firefox"]}, False, True, [], [], True),
        "unsupported": (
            {"desktop_theme": True},
            False,
            False,
            [],
            ["Unsupported architecture"],
            False,
        ),
    }
    tasks = []
    for key, (fields, mutation, cancel, stages, blockers, rescue) in scenarios.items():
        intent = WorkspaceOptions.model_validate(fields).model_dump(mode="json")
        defaults = {
            "intent": intent,
            "section": "workspace",
            "setup_action": "plan",
            "facts": {},
            "changed_stages": stages,
            "mutation_started": mutation,
            "cancel_requested": cancel,
            "blockers": blockers,
            "system_actions": [],
            "apt_changes": [],
            "user_changes": [],
            "latest_debs": {},
            "latest_tools": {},
            "boot_facts": {},
            "execute_run": False,
            "notices": [],
            "request": {"expected": None},
            "ansible_failed_task": {"name": "Prepare next stage"},
            "ansible_failed_result": {"msg": "Synthetic failure"},
        }
        tasks.extend(
            [
                {"ansible.builtin.set_fact": defaults},
                selection,
                plan,
                failure if rescue else report,
                {
                    "ansible.builtin.copy": {
                        "dest": str(run / key),
                        "mode": "0600",
                        "content": "{{ outcome | to_json | native_result | to_json }}",
                    }
                },
            ]
        )
    fixture = run / "status.yml"
    fixture.write_text(
        yaml.safe_dump(
            [{"hosts": "localhost", "gather_facts": False, "become": False, "tasks": tasks}]
        )
    )
    result = subprocess.run(
        [
            str(Path(sys.executable).with_name("ansible-playbook")),
            "-i",
            "localhost,",
            "-c",
            "local",
            str(fixture),
        ],
        cwd=ROOT,
        env={
            "PATH": os.defpath,
            "HOME": str(run),
            "ANSIBLE_CONFIG": str(ROOT / "ansible.cfg"),
            "ANSIBLE_FILTER_PLUGINS": str(ROOT / "ansible/filter_plugins"),
            "ANSIBLE_LOCAL_TEMP": str(run / "local"),
            "ANSIBLE_REMOTE_TEMP": str(run / "remote"),
        },
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {key: json.loads((run / key).read_text()) for key in scenarios}


def test_plan_never_calls_executor_or_creates_files(tmp_path, monkeypatch):
    home = tmp_path / "user"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    for key in ("CONFIG", "DATA", "STATE", "CACHE"):
        monkeypatch.setenv("XDG_" + key + "_HOME", str(home / key.lower()))
    result = asyncio.run(
        AsyncMakeRunner(ROOT).execute(
            Command.capture(Target.WORKSPACE_PLAN, {"schema": 1, "settings": {}, "expected": None})
        )
    )
    assert result.status in ("nothing_selected", "blocked")
    assert not list(home.iterdir())


def test_unsupported_platform_blocks_apply_but_not_navigation(tmp_path, outcomes):
    class Blocked(Tasks):
        async def reply(self, target, payload, **kwargs):
            assert target == Target.WORKSPACE_PLAN
            return outcomes["unsupported"]

    async def run():
        app = app_for(tmp_path, Blocked())
        async with app.run_test(size=(100, 35)) as pilot:
            await pilot.press("ctrl+2")
            await pilot.click("#plan")
            await idle(app, pilot)
            assert "Unsupported architecture" in str(app.query_one("#feedback", Static).render())
            assert app.query_one("#apply", Button).disabled
            await pilot.press("ctrl+1")
            assert app.section == "base"

    asyncio.run(run())


def test_cancel_is_observed_between_completed_stages(outcomes):
    assert outcomes["cancel"]["status"] == "partial"
    assert outcomes["cancel"]["actions"] == [{"kind": "packages_verified", "label": "Packages"}]
    assert "Prepare next stage" in outcomes["cancel"]["blockers"][0]


def test_cancel_before_mutation_has_no_partial_stages(outcomes):
    assert outcomes["cancel_before"]["status"] == "cancelled"
    assert outcomes["cancel_before"]["actions"] == []


def test_partial_failure_never_reports_success(outcomes):
    assert outcomes["partial"]["status"] == "partial"
    assert outcomes["partial"]["actions"] == [{"kind": "packages_verified", "label": "Packages"}]
    assert "Synthetic failure" in outcomes["partial"]["blockers"][0]


def test_second_mutation_is_rejected(tmp_path):
    async def exercise():
        tasks = Tasks()
        app = app_for(tmp_path, tasks)
        async with app.run_test(size=(100, 38)) as pilot:
            await pilot.press("ctrl+2")
            await pilot.click("#plan")
            await idle(app, pilot)
            await pilot.click("#apply")
            tasks.gate = asyncio.Event()
            await pilot.click("#confirm")
            assert app.query_one("#apply", Button).disabled
            await pilot.click("#apply")
            assert [target for target, _, _ in tasks.calls] == [
                Target.WORKSPACE_PLAN,
                Target.WORKSPACE_USER_APPLY,
            ]
            tasks.gate.set()
            await idle(app, pilot)

    asyncio.run(exercise())


def test_preflight_failure_does_not_report_previous_partial_work(outcomes):
    assert outcomes["partial"]["actions"]
    assert outcomes["failed"]["status"] == "blocked"
    assert outcomes["failed"]["actions"] == []
    assert "Prepare next stage" in outcomes["failed"]["blockers"][0]


def test_validation_diagnostics_never_echo_rejected_values():
    with pytest.raises(ValidationError) as caught:
        WorkspaceOptions.model_validate({"token": "synthetic-private-value"})
    assert "synthetic-private-value" not in str(caught.value)


def test_region_changes_have_independent_operations_without_desktop(monkeypatch, capsys):
    from setup_system.infrastructure import regional

    monkeypatch.setattr(regional, "actual", lambda kind: None)
    system_task(
        monkeypatch,
        "plan",
        {
            "section": "base",
            "settings": {"timezone": "Pacific/Tahiti", "keyboard": "us,ru"},
        },
    )
    report = json.loads(capsys.readouterr().out)
    assert set(report["observed"]) == {"timezone", "keyboard"}
    assert [(a["kind"], a["field"], a["value"]) for a in report["actions"]] == [
        ("region", "timezone", "Pacific/Tahiti"),
        ("region", "keyboard", "us,ru"),
    ]
    assert not report["blockers"]


@pytest.mark.parametrize("apps", [[], ["firefox"]])
def test_unselected_desktop_is_absent_from_workspace_plan(tmp_path, apps):
    editor_specs = runpy.run_path(str(ROOT / "ansible/filter_plugins/workspace.py"))["editor_specs"]
    facts = {
        "home": str(tmp_path),
        "xdg": {"config": str(tmp_path / "config"), "data": str(tmp_path / "data")},
    }
    rows = editor_specs(str(ROOT), facts, apps, {"apps": apps}, {})
    assert not rows
    assert not list(tmp_path.iterdir())


def test_empty_workspace_report_distinguishes_no_selection(outcomes):
    assert outcomes["empty"]["status"] == "nothing_selected"
    assert not outcomes["empty"]["actions"] and not outcomes["empty"]["blockers"]


def test_verified_no_changes_does_not_authorize_privileges(outcomes):
    result = outcomes["matched"]
    assert result["status"] == "no_changes" and not result["observed"]["needs_system"]
    with pytest.raises(ValueError, match="no executable changes"):
        PlanSnapshot.capture("workspace", {"apps": ["firefox"]}, result, "revision")


def test_workspace_target_mismatch_stops_before_all_actions(outcomes, monkeypatch):
    from setup_system.infrastructure.native_target import target

    assert not target(
        {"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"}, "sparc", "sparc"
    )[1]
    rejected = outcomes["unsupported"]
    assert rejected["status"] == "blocked"
    with pytest.raises(ValueError):
        PlanSnapshot.capture("workspace", {"desktop_theme": True}, rejected, "revision")


def test_separate_writers_share_one_lock_without_creating_settings(tmp_path):
    import fcntl
    import shlex

    lock = tmp_path / "private run"
    lock.mkdir(mode=0o700)
    (tmp_path / "dpkg_lock.py").write_bytes((ROOT / "libexec/dpkg_lock.py").read_bytes())
    script = tmp_path / "locked.sh"
    script.write_text(
        (ROOT / "libexec/locked.sh")
        .read_text()
        .replace("lock=/run/setup-system", "lock=" + shlex.quote(str(lock)))
    )
    fd = os.open(lock, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        assert subprocess.run(["sh", script, "/bin/true"]).returncode == 75
    finally:
        os.close(fd)
    assert subprocess.run(["sh", script, "/bin/true"]).returncode == 0
    assert not list(lock.iterdir())


def test_changed_draft_during_confirmation_stops_before_authorization(tmp_path):
    async def exercise():
        tasks = Tasks()
        app = app_for(tmp_path, tasks)
        async with app.run_test(size=(100, 38)) as pilot:
            app.query_one("#timezone", Input).value = "Europe/Paris"
            await pilot.click("#plan")
            await idle(app, pilot)
            await pilot.click("#apply")
            app.settings.directory.parent.mkdir(mode=0o700)
            app.settings.directory.mkdir(mode=0o700)
            path = app.settings.directory / "base.toml"
            path.touch(mode=0o600)
            path.write_text('timezone="UTC"\n')
            await pilot.click("#confirm")
            assert not isinstance(app.screen, Password)
            assert "changed" in str(app.query_one("#feedback", Static).render()).lower()
            assert [t for t, _, _ in tasks.calls] == [Target.BASE_PLAN]

    asyncio.run(exercise())


def test_confirmed_happ_upgrade_passes_the_observed_package_version(monkeypatch, capsys):
    from setup_system.infrastructure import happ_facts

    unit = {"LoadState": "loaded", "ActiveState": "inactive"}
    calls = []
    monkeypatch.setattr(happ_facts, "installed_version", lambda: "4.0")
    monkeypatch.setattr(happ_facts, "unit_info", lambda: unit)
    monkeypatch.setattr(
        happ_facts, "require_idle", lambda old, new, facts: calls.append((old, new, facts))
    )
    system_task(
        monkeypatch,
        "happ-inspect",
        {"settings": {"apps": ["happ"]}, "version": "5.0"},
    )
    seen = json.loads(capsys.readouterr().out)
    assert seen == {"installed": "4.0", "unit": unit} and calls == [("4.0", "5.0", unit)]
    plan = PlanSnapshot.capture(
        "workspace",
        {"apps": ["happ"]},
        {
            "status": "ready",
            "actions": [{"kind": "happ_start", "label": "Start the installed Happ service"}],
            "observed": {
                "needs_system": True,
                "needs_user": False,
                "debs": {
                    "happ": {
                        "name": "happ.deb",
                        "url": "https://github.com/verified/happ/releases/download/5.0/happ.deb",
                        "checksum": "sha256:" + "a" * 64,
                        "version": "5.0",
                        "before": seen,
                    }
                },
            },
        },
        None,
    )
    assert plan.payload()["expected"]["observed"]["debs"]["happ"]["before"] == seen


@pytest.mark.parametrize("record", [b"not json", b"[]", b'{"release":"main"}'])
def test_invalid_release_record_blocks_only_privileged_operations(tmp_path, monkeypatch, record):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    path = tmp_path / "setup-system/release.json"
    path.parent.mkdir(mode=0o700)
    path.write_bytes(record)
    runner = AsyncMakeRunner(ROOT)
    with pytest.raises(ValueError, match="installed Debian package"):
        expected = {
            "status": "ready",
            "actions": [{"kind": "region", "field": "timezone", "value": "UTC", "label": "TZ"}],
            "observed": {"needs_system": True, "needs_user": False},
        }
        asyncio.run(
            runner.execute(
                Command.capture(
                    Target.BASE_APPLY, {"schema": 1, "settings": {}, "expected": expected}
                )
            )
        )
    answer = asyncio.run(
        runner.execute(
            Command.capture(
                Target.PROJECT_CHECK,
                {"schema": 1, "settings": {"project": str(tmp_path)}, "expected": None},
            )
        )
    )
    assert answer.status == "ready" and path.read_bytes() == record


@pytest.mark.parametrize(
    "change", ["default_target", "readable_grub_input", "manager_enabled", "unchanged"]
)
def test_login_rejects_drift_between_confirmation_and_root_execution(
    tmp_path, monkeypatch, capsys, change
):
    from types import SimpleNamespace

    from test_desktop_system import system_fixture

    from setup_system.infrastructure import login, login_plan

    system, account, env, commands, snapshot = system_fixture(tmp_path)
    monkeypatch.setattr(login_plan, "inspect_system", lambda home: snapshot)
    monkeypatch.setattr(login, "_require_root", lambda: None)
    read = system.files.read
    inspecting = True
    if change != "manager_enabled":
        system.paths.vendor_unit.unlink()

    def read_as_caller(path, **options):
        if inspecting and path == system.paths.grub_config:
            raise PermissionError("Generated GRUB menu is root-only")
        return read(path, **options)

    monkeypatch.setattr(system.files, "read", read_as_caller)

    user_xdg = {
        key: env["XDG_" + key.upper() + "_HOME"] for key in ("config", "data", "state", "cache")
    }

    def desktop(xdg):
        assert xdg == user_xdg
        return system, account, env

    request = {
        "section": "base",
        "settings": {"hyprland": True, "graphical_login": True},
        "xdg": user_xdg,
    }
    system_task(monkeypatch, "plan", request, desktop=desktop)
    plan = json.loads(capsys.readouterr().out)
    assert "login" in plan["observed"] and plan["actions"]
    inspecting = False
    if change != "manager_enabled":
        system.paths.vendor_unit.write_text("Installed vendor unit\n")
    if change == "default_target":
        commands.default = "rescue.target"
    elif change == "manager_enabled":
        system.paths.display_manager.parent.mkdir(parents=True, exist_ok=True)
        system.paths.display_manager.symlink_to(system.paths.vendor_unit)
    elif change == "readable_grub_input":
        system.paths.grub_default.write_text("GRUB_TIMEOUT=15\n")

    def guard(path, relative):
        assert path == ROOT / relative
        assert relative == "ansible/files/system_contract.py"
        assert system.paths.boundary.is_relative_to(tmp_path)

    def apply():
        system_task(
            monkeypatch,
            "login",
            request | {"observed": plan["observed"], "actions": plan["actions"]},
            desktop=desktop,
            require_installed_resource=guard,
            current_target=lambda: ("amd64", True),
            os=SimpleNamespace(geteuid=lambda: 0),
        )

    if change == "unchanged":
        apply()
        assert system.paths.receipt.exists()
    else:
        with pytest.raises(ValueError, match="changed after consent"):
            apply()
        assert not system.paths.receipt.exists() and not system.paths.config.exists()
        assert not any("set-default" in argv or "enable" in argv for argv in commands.calls)
