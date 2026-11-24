import asyncio
import os
import threading
from pathlib import Path

import pytest
from textual.widgets import Button, Input, Static, TabbedContent

from setup_system.app import SetupApp, main
from setup_system.runner import Target
from setup_system.settings import Settings
from setup_system.suggestions import Catalog
from setup_system.ui.dialogs import Consent, Password
from setup_system.ui.forms import CompletionInput, WorkspaceForm


class Tasks:
    def __init__(self):
        self.calls = []
        self.gate = None
        self.exit_status = "complete"

    def run(self, target, payload, *, password=None, cancel=None):
        self.calls.append(
            (target, payload, password.take() == b"synthetic fixture" if password else None)
        )
        if target.value.endswith("-inspect"):
            return {
                "status": "ready",
                "observed": {
                    "outputs": [{"output": "DP-1", "modes": ["1920x1080@60"]}],
                    "timezones": ["Europe/Paris"],
                    "keyboards": ["de", "fr"],
                    "locales": ["de_DE.UTF-8"],
                },
            }
        if target.value.endswith("-plan"):
            return {
                "status": "ready",
                "actions": ["Apply the selected component"],
                "observed": {
                    "uid": os.getuid(),
                    "needs_system": target == Target.BASE_PLAN,
                    "needs_user": target != Target.BASE_PLAN,
                },
            }
        if self.gate is not None:
            assert self.gate.wait(10)
        return {"status": "partial" if cancel.is_set() else self.exit_status}


def app_for(tmp_path, tasks):
    home = tmp_path / "Тестовый Home"
    home.mkdir(mode=0o700)
    root = Path(__file__).resolve().parents[1] / "config"
    return SetupApp(tasks, Settings(home, home / "config"), Catalog(root))


async def idle(app, pilot):
    for _ in range(100):
        await pilot.pause(0.01)
        if app.cancel_event is None:
            return
    raise AssertionError("Worker did not finish")


def test_plan_uses_reviewed_snapshot_and_supports_tab_switch_cancel(tmp_path):
    async def scenario():
        tasks = Tasks()
        app = app_for(tmp_path, tasks)
        async with app.run_test(size=(110, 42)) as pilot:
            field = app.query_one("#timezone", Input)
            field.value = "UTC"
            await pilot.click("#plan")
            await idle(app, pilot)
            assert app.pending is not None
            field.value = "Europe/Paris"
            await pilot.click("#apply")
            assert isinstance(app.screen, Consent)
            assert not list(app.screen.query("#password"))
            await pilot.click("#confirm")
            assert isinstance(app.screen, Password)
            await pilot.click("#authorize")
            assert tasks.calls[-1][0] == Target.BASE_PLAN
            await pilot.press("escape")
            assert tasks.calls[-1][0] == Target.BASE_PLAN
            await pilot.click("#apply")
            await pilot.click("#confirm")
            password = app.screen.query_one("#password", Input)
            assert password.password
            password.value = "synthetic fixture"
            tasks.gate = threading.Event()
            await pilot.click("#authorize")
            assert password.value == ""
            await pilot.pause()
            assert tasks.calls[-1] == (
                Target.BASE_APPLY,
                {
                    "schema": 1,
                    "settings": {
                        "hyprland": False,
                        "graphical_login": False,
                        "plymouth": False,
                        "timezone": "UTC",
                    },
                    "expected": {
                        "status": "ready",
                        "actions": ["Apply the selected component"],
                        "observed": {"uid": os.getuid(), "needs_system": True, "needs_user": False},
                    },
                },
                True,
            )
            await pilot.press("ctrl+2")
            assert app.query_one(TabbedContent).active == "workspace"
            await pilot.press("ctrl+1")
            assert field.value == "Europe/Paris"
            await pilot.click("#cancel")
            assert app.cancel_event.is_set()
            tasks.gate.set()
            await idle(app, pilot)
            assert str(app.query_one("#feedback", Static).render()).find("Partial") >= 0
            assert not app.settings.directory.exists()

    asyncio.run(scenario())


def test_user_restore_and_changed_settings_block_stale_plan(tmp_path):
    async def scenario():
        tasks = Tasks()
        app = app_for(tmp_path, tasks)
        async with app.run_test(size=(105, 36)) as pilot:
            await pilot.press("ctrl+2")
            app.query_one("#backup", Input).value = "20261124T100000Z-0123abcd"
            app.query_one("#restore-plan", Button).press()
            await idle(app, pilot)
            assert tasks.calls[-1][0] == Target.RESTORE_PLAN
            await pilot.click("#apply")
            assert isinstance(app.screen, Consent)
            assert not list(app.screen.query("#password"))
            await pilot.click("#confirm")
            await idle(app, pilot)
            assert tasks.calls[-1][0] == Target.RESTORE_APPLY
            assert tasks.calls[-1][1]["settings"] == {"backup_id": "20261124T100000Z-0123abcd"}
            assert tasks.calls[-1][2] is None
            await pilot.click("#plan")
            await idle(app, pilot)
            await pilot.click("#apply")
            assert not list(app.screen.query("#password"))
            app.settings.directory.parent.mkdir(mode=0o700)
            app.settings.directory.mkdir(mode=0o700)
            (app.settings.directory / "workspace.toml").write_text(
                'schema_version = 1\napps = ["firefox"]'
            )
            (app.settings.directory / "workspace.toml").chmod(0o600)
            await pilot.click("#confirm")
            assert tasks.calls[-1][0] == Target.WORKSPACE_PLAN
            assert "Reload" in str(app.query_one("#feedback", Static).render())

    asyncio.run(scenario())


def test_completion_refresh_modes_paths_and_keyboard(tmp_path):
    async def scenario():
        app = app_for(tmp_path, Tasks())
        async with app.run_test(size=(105, 36)) as pilot:
            await pilot.click("#inspect")
            await idle(app, pilot)
            field = app.query_one("#timezone", CompletionInput)
            field.focus()
            field.value = "Europe/P"
            field.cursor_position = len(field.value)
            await pilot.pause()
            await pilot.press("escape")
            assert field.value == "Europe/P"
            field.value = "Europe/Pa"
            field.cursor_position = len(field.value)
            await pilot.pause()
            await pilot.press("tab")
            assert field.value == "Europe/Paris"
            field.value = "Europe/P"
            field.cursor_position = len(field.value)
            await pilot.pause()
            await pilot.press("enter")
            assert field.value == "Europe/Paris"
            assert app.suggestions.modes["DP-1"] == ("1920x1080@60",)
            await pilot.press("ctrl+2")
            form = app.query_one(WorkspaceForm)
            app.query_one("#output", Input).value = "DP-1"
            await pilot.pause()
            app.query_one("#mode", Input).value = "1024x768@99"
            with pytest.raises(ValueError, match="advertised"):
                form.update_layout(False)
            app.query_one("#mode", Input).value = "1920x1080@60"
            form.update_layout(False)
            assert form.options().monitors[0].output == "DP-1"
            app.suggestions.refresh("workspace", {"outputs": []})
            assert not app.suggestions.outputs.values
            assert app.suggestions.project_values("") == ()
            project = app.settings.home / "Архив 2"
            project.mkdir()
            assert str(project) in app.suggestions.project_values(str(project)[:-1])
            project.rmdir()
            assert str(project) in app.suggestions.project_values(str(project)[:-1])
            app.suggestions.refresh("workspace", {})
            assert str(project) not in app.suggestions.project_values(str(project)[:-1])

    asyncio.run(scenario())


def test_no_terminal_without_launching_ui(monkeypatch, capsys):
    monkeypatch.setattr(os, "getuid", lambda: 1234)
    monkeypatch.setattr(os, "geteuid", lambda: 1234)
    assert main() == 2
    assert "interactive terminal" in capsys.readouterr().err


def test_user_only_workspace_quit_waits_until_writer_reaches_a_boundary(tmp_path):
    async def scenario():
        tasks = Tasks()
        app = app_for(tmp_path, tasks)
        async with app.run_test(size=(105, 36)) as pilot:
            await pilot.press("ctrl+2")
            await pilot.click("#plan")
            await idle(app, pilot)
            await pilot.click("#apply")
            tasks.gate = threading.Event()
            await pilot.click("#confirm")
            assert tasks.calls[-1][0] == Target.WORKSPACE_USER_APPLY
            assert tasks.calls[-1][2] is None
            assert "cleanup" not in tasks.calls[-1][1]["settings"]
            await pilot.press("ctrl+q")
            assert app.quit_when_finished
            assert app.cancel_event.is_set()
            assert app.is_running
            tasks.gate.set()
            await asyncio.to_thread(tasks.gate.wait, 1)

    asyncio.run(scenario())


def test_cleanup_has_its_own_snapshot_and_preserves_other_form_fields(tmp_path):
    async def scenario():
        app = app_for(tmp_path, Tasks())
        async with app.run_test(size=(105, 36)) as pilot:
            await pilot.press("ctrl+2")
            app.query_one("#rounding", Input).value = ""
            app.query_one("#cleanup-plan", Button).press()
            await idle(app, pilot)
            assert app.runner.calls[-1][1]["settings"] == {"cleanup": [], "cleanup_mode": "remove"}
            assert app.query_one("#rounding", Input).value == ""

    asyncio.run(scenario())


def test_only_base_user_files_use_unprivileged_make_after_consent(tmp_path):
    class PersonalBase(Tasks):
        def run(self, target, payload, **flags):
            reply = super().run(target, payload, **flags)
            if target == Target.BASE_PLAN:
                reply["observed"].update(needs_system=False, needs_user=True)
            return reply

    async def scenario():
        tasks = PersonalBase()
        app = app_for(tmp_path, tasks)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.click("#plan")
            await idle(app, pilot)
            await pilot.click("#apply")
            await pilot.click("#confirm")
            await idle(app, pilot)
            assert tasks.calls[-1][0].value == "base-user-apply"
            assert tasks.calls[-1][2] is None

    asyncio.run(scenario())


def test_base_user_apply_enters_real_play_without_a_become_prompt(tmp_path, monkeypatch):
    from setup_system.runner import MakeRunner

    monkeypatch.setenv("HOME", str(tmp_path))
    for key in ("CONFIG", "DATA", "STATE", "CACHE"):
        monkeypatch.setenv("XDG_" + key + "_HOME", str(tmp_path / key.lower()))
    runner = MakeRunner()
    reply = runner.run(Target("base-user-apply"), {"schema": 1, "settings": {}, "expected": None})
    assert reply["status"] == "blocked" and reply["blockers"]
    assert not list(tmp_path.iterdir())
