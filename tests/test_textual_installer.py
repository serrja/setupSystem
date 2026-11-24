"""Headless Textual tests preserve navigation, snapshots, consent and safe cancellation."""

import asyncio
from asyncio import Event
from pathlib import Path

import pytest
from test_native_ui import Tasks, idle
from textual.widgets import Button, Checkbox, Input, SelectionList, Static

from setup_system.app import SetupApp
from setup_system.runner import Target
from setup_system.ui.dialogs import Consent
from setup_system.ui.forms import WorkspaceForm

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def private_home(tmp_path, monkeypatch):
    home = tmp_path / "headless home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    for scope in ("config", "data", "state", "cache"):
        monkeypatch.setenv("XDG_" + scope.upper() + "_HOME", str(home / scope))


def test_open_and_navigation_have_no_execution_or_side_effects() -> None:
    async def run():
        tasks = Tasks()
        app = SetupApp(runner=tasks)
        async with app.run_test(size=(110, 42)) as pilot:
            await pilot.press("ctrl+2")
            assert app.section == "workspace"
            await pilot.press("ctrl+1")
            assert app.section == "base" and not tasks.calls
            assert not any(Path.home().iterdir())

    asyncio.run(run())


def test_headless_plan_does_not_confirm_or_apply() -> None:
    async def run():
        tasks = Tasks()
        app = SetupApp(runner=tasks)
        async with app.run_test(size=(110, 42)) as pilot:
            await pilot.click("#plan")
            await idle(app, pilot)
            assert [(t, p["expected"], secret) for t, p, secret in tasks.calls] == [
                (Target.BASE_PLAN, None, None)
            ]
            assert not isinstance(app.screen, Consent) and app.pending is not None
            assert app.cancel_event is None

    asyncio.run(run())


def test_screen_switch_keeps_active_plan_and_future_intentions_separate() -> None:
    async def run():
        tasks = Tasks()
        app = SetupApp(runner=tasks)
        async with app.run_test(size=(110, 42)) as pilot:
            await pilot.press("ctrl+2")
            await pilot.click("#plan")
            await idle(app, pilot)
            await pilot.click("#apply")
            assert isinstance(app.screen, Consent)
            tasks.gate = Event()
            await pilot.click("#confirm")
            await pilot.press("ctrl+1")
            assert app.section == "base" and app.cancel_event is not None
            await pilot.press("ctrl+2")
            app.query_one("#opacity", Input).value = "0.80"
            assert tasks.calls[-1][0] == Target.WORKSPACE_USER_APPLY
            assert tasks.calls[-1][1]["settings"]["theme"]["opacity"] == 0.96
            assert app.query_one(WorkspaceForm).options().theme.opacity == 0.80
            assert app.query_one("#apply", Button).disabled
            tasks.gate.set()
            await idle(app, pilot)
            assert "Complete" in str(app.query_one("#feedback", Static).render())

    asyncio.run(run())


def test_cancel_and_quit_wait_for_safe_boundary() -> None:
    async def run():
        tasks = Tasks()
        app = SetupApp(runner=tasks)
        async with app.run_test(size=(110, 42)) as pilot:
            await pilot.press("ctrl+2")
            await pilot.click("#plan")
            await idle(app, pilot)
            await pilot.click("#apply")
            tasks.gate = Event()
            await pilot.click("#confirm")
            await app.action_quit()
            assert app.quit_when_finished and app.cancel_event.is_set() and app.is_running
            tasks.gate.set()
            async with asyncio.timeout(5):
                while app.cancel_event is not None:
                    await asyncio.sleep(0.01)

    asyncio.run(run())


def test_confirmation_cancel_never_reaches_mutation() -> None:
    async def run():
        tasks = Tasks()
        app = SetupApp(runner=tasks)
        async with app.run_test(size=(110, 42)) as pilot:
            await pilot.click("#plan")
            await idle(app, pilot)
            await pilot.click("#apply")
            await pilot.press("escape")
            assert [t for t, _, _ in tasks.calls] == [Target.BASE_PLAN]
            assert "cancelled" in str(app.query_one("#feedback", Static).render())

    asyncio.run(run())


def test_failed_worker_keeps_diagnostics_and_navigation_available() -> None:
    class Failed(Tasks):
        async def run(self, *args, **kwargs):
            raise ValueError("Synthetic resolver failure")

    async def run():
        app = SetupApp(runner=Failed())
        async with app.run_test(size=(110, 42)) as pilot:
            await pilot.click("#plan")
            await idle(app, pilot)
            assert "Synthetic resolver failure" in str(app.query_one("#feedback", Static).render())
            assert app.pending is None and app.cancel_event is None
            await pilot.press("ctrl+2")
            assert app.section == "workspace"

    asyncio.run(run())


def test_same_form_intentions_survive_repeated_navigation() -> None:
    async def run():
        app = SetupApp(runner=Tasks())
        async with app.run_test(size=(110, 42)) as pilot:
            await app.workers.wait_for_complete()
            app.query_one("#hyprland", Checkbox).value = True
            await pilot.press("ctrl+2")
            app.query_one("#apps", SelectionList).select("firefox")
            await pilot.press("ctrl+1")
            await pilot.press("ctrl+2")
            assert app.options("base").hyprland
            assert "firefox" in app.options("workspace").apps

    asyncio.run(run())


def test_action_buttons_fit_a_standard_terminal() -> None:
    async def run():
        app = SetupApp(runner=Tasks())
        async with app.run_test(size=(80, 25)) as pilot:
            await pilot.pause()
            for name in ("inspect", "plan", "apply", "save", "reload", "cancel"):
                region = app.query_one("#" + name, Button).region
                assert 0 <= region.x < region.right <= 80
                assert 0 <= region.y < region.bottom <= 25

    asyncio.run(run())


def test_recovery_form_previews_and_preserves_conflicting_edits(tmp_path, monkeypatch) -> None:
    from setup_system.infrastructure.managed_files import apply_files

    target = Path.home() / "config/created.txt"
    operation = apply_files({target: b"managed"})
    target.write_bytes(b"later personal edit")

    async def run():
        app = SetupApp()
        async with app.run_test(size=(110, 48)) as pilot:
            await pilot.press("ctrl+2")
            app.query_one("#backup", Input).value = operation.name
            app.query_one("#restore-plan", Button).press()
            async with asyncio.timeout(20):
                while "conflict" not in str(app.query_one("#feedback", Static).render()).lower():
                    await pilot.pause()
            assert app.pending is None and app.query_one("#apply", Button).disabled
            assert target.read_bytes() == b"later personal edit"

    asyncio.run(run())


def test_project_git_and_template_forms_produce_only_their_required_inputs(tmp_path) -> None:
    from setup_system.models import validate_project_settings
    from setup_system.ui.projects import ProjectTools

    async def run():
        app = SetupApp(runner=Tasks())
        async with app.run_test(size=(110, 48)) as pilot:

            async def choose(group, target, fields):
                selection = asyncio.get_running_loop().create_future()
                app.push_screen(
                    ProjectTools(group, {}, {"python": "Python"}, {}), selection.set_result
                )
                await pilot.pause()
                form = app.screen
                for key, value in fields.items():
                    field = form.query_one("#" + key)
                    field.value = value
                form.query_one("#" + target, Button).press()
                return (await asyncio.wait_for(selection, 5)).values

            git = {
                "repository": str(tmp_path),
                "profile-name": "work",
                "owner-directory": str(tmp_path.parent),
            }
            assert await choose("git", "git-inspect", git) == {"repository": str(tmp_path)}
            assert await choose("git", "git-configure-plan", git) == {
                "name": "work",
                "repository": str(tmp_path),
                "owner_directory": str(tmp_path.parent),
            }
            template = {"template": "python", "project-name": "sample"}
            assert await choose("templates", "template-cache-plan", template) == {
                "template": "python",
                "offline": False,
            }
            incomplete = await choose("templates", "template-create-plan", template)
            with pytest.raises(ValueError):
                validate_project_settings("template-create", incomplete)
            template.update({"destination": str(tmp_path / "new"), "python-profile": "service"})
            request = await choose("templates", "template-create-plan", template)
            assert validate_project_settings("template-create", request) == {
                "template": "python",
                "offline": False,
                "destination": str(tmp_path / "new"),
                "project_name": "sample",
                "python_profile": "service",
            }

    asyncio.run(run())


def test_external_intention_edit_refuses_apply_before_confirmation() -> None:
    async def run():
        tasks = Tasks()
        app = SetupApp(runner=tasks)
        async with app.run_test(size=(110, 42)) as pilot:
            await pilot.click("#plan")
            await idle(app, pilot)
            path = Path.home() / "config/setup-system/base.toml"
            path.parent.parent.mkdir(mode=0o700)
            path.parent.mkdir(mode=0o700)
            path.touch(mode=0o600)
            path.write_text('timezone = "Etc/UTC"\n')
            await pilot.click("#apply")
            assert not isinstance(app.screen, Consent) and app.cancel_event is None
            assert [t for t, _, _ in tasks.calls] == [Target.BASE_PLAN]
            assert path.read_text() == 'timezone = "Etc/UTC"\n'

    asyncio.run(run())
