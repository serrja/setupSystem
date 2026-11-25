import asyncio
import json
import os
from pathlib import Path

import pytest
from textual.widgets import Button, Input, Static, TabbedContent

from setup_system.app import SetupApp, main
from setup_system.domain.observations import NativeObservations
from setup_system.models import TaskResult
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

    async def check_authorization(self):
        return Path("/usr/lib/setup-system")

    async def execute(self, command, *, password=None, cancel=None, on_event=None):
        target = command.target
        payload = json.loads(command.request_json)
        return TaskResult.model_validate(
            await self.reply(target, payload, password=password, cancel=cancel)
        )

    async def reply(self, target, payload, *, password=None, cancel=None):
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
            action = {"kind": "user_file", "path": "test", "label": "Apply the selected component"}
            if target == Target.BASE_PLAN:
                action = {
                    "kind": "region",
                    "field": "timezone",
                    "value": "UTC",
                    "label": "Apply the selected component",
                }
            elif target == Target.RESTORE_PLAN:
                action = {
                    "kind": "restore",
                    "path": "/home/example/restored",
                    "operation": "would-restore",
                    "label": "Restore the managed file",
                }
            elif target == Target.CLEANUP_PLAN:
                action = {"kind": "apt", "record": "Remv nano [7.2]", "label": "Remove nano"}
            return {
                "status": "ready",
                "actions": [action],
                "observed": {
                    "uid": os.getuid(),
                    "needs_system": target in {Target.BASE_PLAN, Target.CLEANUP_PLAN},
                    "needs_user": target not in {Target.BASE_PLAN, Target.CLEANUP_PLAN},
                },
            }
        if self.gate is not None:
            await asyncio.wait_for(self.gate.wait(), timeout=10)
        return {"status": "partial" if cancel.is_set() else self.exit_status}


def app_for(tmp_path, tasks):
    home = tmp_path / "Тестовый Home"
    home.mkdir(mode=0o700)
    root = Path(__file__).resolve().parents[1] / "config"
    return SetupApp(tasks, Settings(home, home / "config"), Catalog(root))


async def idle(app, pilot):
    for _ in range(100):
        await pilot.pause(0.01)
        if app.cancel_event is None and app.reviewing is None:
            return
    raise AssertionError("Worker did not finish")


async def press_plan(app, pilot, button_id, task):
    """Wait for the requested preview after the button message bubbles up to the app."""
    button = app.query_one(button_id, Button)
    assert button.display and not button.disabled
    button.press()
    async with asyncio.timeout(5):
        while app.pending is None or app.pending.task != task or app.cancel_event is not None:
            await pilot.pause()


def test_plan_uses_reviewed_snapshot_and_supports_tab_switch_cancel(tmp_path):
    async def scenario():
        tasks = Tasks()
        app = app_for(tmp_path, tasks)
        async with app.run_test(size=(110, 42)) as pilot:
            await app.workers.wait_for_complete()
            field = app.query_one("#timezone", Input)
            field.value = "UTC"
            await pilot.click("#plan")
            await idle(app, pilot)
            assert app.pending is not None
            await pilot.press("ctrl+2")
            assert app.pending is not None
            await pilot.press("ctrl+1")
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
            tasks.gate = asyncio.Event()
            await pilot.click("#authorize")
            assert password.value == ""
            field.value = "Europe/Paris"
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
                        "actions": [
                            {
                                "kind": "region",
                                "field": "timezone",
                                "value": "UTC",
                                "label": "Apply the selected component",
                            }
                        ],
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


@pytest.mark.parametrize("phase", ["preview", "consent", "password"])
def test_changed_live_selection_requires_a_new_preview(tmp_path, phase):
    async def scenario():
        tasks = Tasks()
        app = app_for(tmp_path, tasks)
        async with app.run_test(size=(110, 42)) as pilot:
            await app.workers.wait_for_complete()
            field = app.query_one("#timezone", Input)
            field.value = "UTC"
            await press_plan(app, pilot, "#plan", "base")
            if phase != "preview":
                await pilot.click("#apply")
                assert isinstance(app.screen, Consent)
            if phase == "password":
                await pilot.click("#confirm")
                assert isinstance(app.screen, Password)
                app.screen.query_one("#password", Input).value = "synthetic fixture"
            field.value = "Europe/Paris"
            if phase != "preview":
                await pilot.click("#confirm" if phase == "consent" else "#authorize")
            await idle(app, pilot)
            assert app.pending is None and app.query_one("#apply", Button).disabled
            assert [entry[0] for entry in tasks.calls] == [Target.BASE_PLAN]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("admin-account", " alice "),
        ("admin-account", "alice\n"),
        ("timezone", " UTC "),
        ("timezone", "  "),
        ("project", "/tmp/selected\n"),
    ],
)
def test_forms_preserve_invalid_input_until_validation(tmp_path, name, value):
    async def scenario():
        tasks = Tasks()
        app = app_for(tmp_path, tasks)
        async with app.run_test(size=(110, 42)) as pilot:
            await app.workers.wait_for_complete()
            if name == "project":
                await pilot.press("ctrl+2")
            field = app.query_one("#" + name, Input)
            field.value = value
            app.query_one(
                {"admin-account": "#admin-plan", "project": "#project-check", "timezone": "#plan"}[
                    name
                ],
                Button,
            ).press()
            await pilot.pause()
            assert not tasks.calls and field.value == value
            assert app.pending is None

    asyncio.run(scenario())


def test_expanded_project_path_preserves_directory_name_and_home(tmp_path):
    async def scenario():
        tasks = Tasks()
        app = app_for(tmp_path, tasks)
        async with app.run_test(size=(110, 42)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("ctrl+2")
            field = app.query_one("#project", Input)
            field.value = "~//Example Project "
            app.query_one("#project-check", Button).press()
            await idle(app, pilot)
            assert (
                tasks.calls[-1][1]["settings"]["project"]
                == f"{app.suggestions.home}//Example Project "
            )

    asyncio.run(scenario())


def test_user_restore_and_changed_settings_block_stale_plan(tmp_path):
    async def scenario():
        tasks = Tasks()
        app = app_for(tmp_path, tasks)
        async with app.run_test(size=(105, 36)) as pilot:
            await pilot.press("ctrl+2")
            app.query_one("#backup", Input).value = "20261124T100000Z-0123abcd"
            await press_plan(app, pilot, "#restore-plan", "restore")
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
            await app.suggestions.refresh("workspace", NativeObservations(outputs=()))
            assert not app.suggestions.modes
            assert await app.suggestions.project_values("") == ()
            project = app.settings.home / "Архив 2"
            project.mkdir()
            assert str(project) in await app.suggestions.project_values(str(project)[:-1])
            project.rmdir()
            assert str(project) in await app.suggestions.project_values(str(project)[:-1])
            await app.suggestions.refresh("workspace", NativeObservations())
            assert str(project) not in await app.suggestions.project_values(str(project)[:-1])

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
            tasks.gate = asyncio.Event()
            await pilot.click("#confirm")
            assert tasks.calls[-1][0] == Target.WORKSPACE_USER_APPLY
            assert tasks.calls[-1][2] is None
            assert "cleanup" not in tasks.calls[-1][1]["settings"]
            await pilot.press("ctrl+q")
            assert app.quit_when_finished
            assert app.cancel_event.is_set()
            assert app.is_running
            # The terminal may already be dismantling its view past this point.
            await app.query_one("#feedback").remove()
            tasks.gate.set()
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
            assert app.cancel_event is None

    asyncio.run(scenario())


def test_cleanup_has_its_own_snapshot_and_preserves_other_form_fields(tmp_path):
    async def scenario():
        app = app_for(tmp_path, Tasks())
        async with app.run_test(size=(105, 36)) as pilot:
            await pilot.press("ctrl+2")
            app.query_one("#rounding", Input).value = ""
            await press_plan(app, pilot, "#cleanup-plan", "cleanup")
            assert app.runner.calls[-1][1]["settings"] == {"cleanup": [], "cleanup_mode": "remove"}
            assert app.query_one("#rounding", Input).value == ""

    asyncio.run(scenario())


def test_only_base_user_files_use_unprivileged_make_after_consent(tmp_path):
    class PersonalBase(Tasks):
        async def reply(self, target, payload, **flags):
            reply = await super().reply(target, payload, **flags)
            if target == Target.BASE_PLAN:
                reply["observed"].update(needs_system=False, needs_user=True)
                reply["actions"] = [
                    {"kind": "user_file", "path": "test", "label": "Apply the selected component"}
                ]
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
    from setup_system.infrastructure.operation_outcome import read_last_outcome
    from setup_system.runner import AsyncMakeRunner, Command

    monkeypatch.setenv("HOME", str(tmp_path))
    for key in ("CONFIG", "DATA", "STATE", "CACHE"):
        monkeypatch.setenv("XDG_" + key + "_HOME", str(tmp_path / key.lower()))
    runner = AsyncMakeRunner()
    expected = {
        "status": "ready",
        "actions": [{"kind": "user_file", "path": "test", "label": "Fixture"}],
        "observed": {"needs_system": False, "needs_user": True},
    }
    reply = asyncio.run(
        runner.execute(
            Command.capture(
                Target.BASE_USER_APPLY, {"schema": 1, "settings": {}, "expected": expected}
            )
        )
    )
    assert reply.status == "blocked" and reply.blockers
    recorded = asyncio.run(read_last_outcome())
    assert recorded is not None
    assert recorded.target is Target.BASE_USER_APPLY and recorded.status is reply.status
    state = tmp_path / "state"
    folder = state / "setup-system"
    assert set(tmp_path.rglob("*")) == {state, folder, folder / "last-operation.json"}


def test_administrative_consent_precedes_the_dedicated_root_prompt(tmp_path):
    from textual.widgets import Label

    class AdminTasks(Tasks):
        async def reply(self, target, payload, **flags):
            result = await super().reply(target, payload, **flags)
            if target == Target.ADMIN_PLAN:
                return {
                    "status": "ready",
                    "actions": [
                        {
                            "kind": "admin",
                            "username": "alice",
                            "label": "Grant alice administrative access",
                        }
                    ],
                    "observed": {
                        "needs_system": True,
                        "needs_user": False,
                        "boot_identity": "a" * 64,
                        "account": {
                            "username": "alice",
                            "uid": 1002,
                            "gid": 1002,
                            "sudo_gid": 27,
                            "home": "/home/alice",
                            "shell": "/bin/sh",
                            "member": False,
                        },
                    },
                }
            return result

    async def scenario():
        tasks = AdminTasks()
        app = app_for(tmp_path, tasks)
        async with app.run_test(size=(110, 45)) as pilot:
            await app.workers.wait_for_complete()
            app.query_one("#admin-account", Input).value = "alice"
            await press_plan(app, pilot, "#admin-plan", "admin")
            assert app.pending.task == "admin"
            await pilot.click("#apply")
            assert isinstance(app.screen, Consent)
            view = str(app.screen.query_one("#review Static", Static).render())
            assert all(fact in view for fact in ("alice", "1002", "/home/alice", "/bin/sh"))
            assert not list(app.screen.query(Input))
            await pilot.click("#confirm")
            assert isinstance(app.screen, Password) and app.screen.root_account
            assert "Root password" in str(app.screen.query_one(Label).render())
            await pilot.press("escape")
            assert [call[0] for call in tasks.calls] == [Target.ADMIN_PLAN]
            await pilot.click("#apply")
            await pilot.click("#confirm")
            field = app.screen.query_one(Input)
            field.value = "synthetic fixture"
            await pilot.click("#authorize")
            await idle(app, pilot)
            assert not field.value
            target, request, received = tasks.calls[-1]
            assert target == Target.ADMIN_APPLY and received
            assert request["settings"] == {"username": "alice"}
            assert "synthetic fixture" not in repr(request)

    asyncio.run(scenario())


def test_settings_change_after_password_prompt_expires_the_response(tmp_path):
    from setup_system.runner import BecomePassword

    async def scenario():
        tasks = Tasks()
        app = app_for(tmp_path, tasks)
        async with app.run_test(size=(110, 45)) as pilot:
            await pilot.click("#plan")
            await idle(app, pilot)
            await pilot.click("#apply")
            await pilot.click("#confirm")
            assert isinstance(app.screen, Password)
            directory = app.settings.directory
            directory.mkdir(mode=0o700, parents=True)
            config = directory / "base.toml"
            config.write_text('schema_version = 1\ntimezone = "UTC"\n')
            config.chmod(0o600)
            answer = BecomePassword("synthetic fixture")
            app.screen.dismiss(answer)
            await idle(app, pilot)
            with pytest.raises(RuntimeError, match="expired"):
                answer.take()
            assert tasks.calls[-1][0] == Target.BASE_PLAN and app.pending is None

    asyncio.run(scenario())


def test_reload_requires_explicit_discard_and_cancel_preserves_invalid_edits(tmp_path):
    async def scenario():
        app = app_for(tmp_path, Tasks())
        async with app.run_test(size=(110, 42)) as pilot:
            await app.workers.wait_for_complete()
            field = app.query_one("#timezone", Input)
            field.value = "unfinished value"
            await pilot.click("#reload")
            assert app.screen.query("#discard-edits")
            await pilot.press("escape")
            assert field.value == "unfinished value"
            await pilot.click("#reload")
            await pilot.click("#discard-edits")
            await app.workers.wait_for_complete()
            assert field.value == ""

    asyncio.run(scenario())


def test_apply_from_another_tab_identifies_the_stage_and_runs_only_once(tmp_path):
    async def scenario():
        tasks = Tasks()
        app = app_for(tmp_path, tasks)
        async with app.run_test(size=(110, 42)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.click("#plan")
            await idle(app, pilot)
            await pilot.press("ctrl+2")
            apply = app.query_one("#apply", Button)
            assert "base" in str(apply.label).lower()
            apply.press()
            apply.press()
            await pilot.pause()
            assert isinstance(app.screen, Consent)
            assert "base" in str(app.screen.query_one("Label").render()).lower()
            await pilot.click("#confirm")
            app.screen.query_one("#password", Input).value = "fixture"
            await pilot.click("#authorize")
            await idle(app, pilot)
            assert [target for target, *_ in tasks.calls].count(Target.BASE_APPLY) == 1

    asyncio.run(scenario())
