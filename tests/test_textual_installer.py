"""Headless Textual tests preserve navigation, snapshots, consent and safe cancellation."""

import asyncio
from pathlib import Path
from threading import Event

import pytest
from textual.widgets import Button, Checkbox, Input, TabbedContent

from setup_system.composition import build_app
from setup_system.domain.operations import InstallRequest, Operation
from setup_system.domain.settings import Section
from setup_system.tui.installer import Prompt

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def private_home(tmp_path, monkeypatch):
    home = tmp_path / "headless home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for scope in ("config", "data", "state", "cache"):
        monkeypatch.setenv("XDG_" + scope.upper() + "_HOME", str(home / scope))


class Service:
    def __init__(self):
        self.requests = []
        self.started = Event()
        self.release = Event()
        self.block = False
        self.code = 0
        self.interaction = None
        self.cancel = Event()
        self.completed = []
        self.status = None

    def plan(self, request):
        from setup_system.domain.operations import WorkstationPlan

        return WorkstationPlan(
            section=request.section,
            system=request.system,
            user=request.settings,
            actions=(),
            observations=(),
        )

    def run(self, request):
        self.requests.append(request)
        self.started.set()
        if request.operation == Operation.APPLY and not self.interaction.confirm(
            "Apply synthetic fixture?"
        ):
            return 2
        while self.block and not self.release.wait(0.01):
            if self.interaction.cancelled:
                return 3
        self.interaction.write("Synthetic operation complete")
        return self.code


def app_for(service):
    return build_app(ROOT, InstallRequest(operation=Operation.PLAN), service=service)


def test_open_and_navigation_have_no_execution_or_side_effects() -> None:
    async def run():
        service = Service()
        app = app_for(service)
        async with app.run_test(size=(110, 42)) as pilot:
            assert not service.requests
            await pilot.press("ctrl+2")
            assert app.query_one("#sections", TabbedContent).active == "workspace"
            await pilot.press("ctrl+1")
            assert app.query_one("#sections", TabbedContent).active == "base"
            assert not service.requests

    asyncio.run(run())


def test_headless_plan_does_not_confirm_or_apply() -> None:
    async def run():
        service = Service()
        app = app_for(service)
        async with app.run_test(size=(110, 42)) as pilot:
            await pilot.click("#plan")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert [request.operation for request in service.requests] == [Operation.PLAN]
            assert not isinstance(app.screen, Prompt)
            assert app.last_code == 0 and not app.busy

    asyncio.run(run())


def test_screen_switch_keeps_active_plan_and_future_intentions_separate() -> None:
    async def run():
        service = Service()
        service.block = True
        app = app_for(service)
        async with app.run_test(size=(110, 42)) as pilot:
            await pilot.click("#apply")
            await pilot.pause()
            assert isinstance(app.screen, Prompt)
            await pilot.click("#confirm")
            await pilot.pause()
            assert app.busy
            await pilot.press("ctrl+2")
            assert app.query_one("#sections", TabbedContent).active == "workspace"
            app.query_one("#opacity", Input).value = "0.80"
            assert app.running_request.settings.theme.opacity == 0.96
            assert service.requests[0].section == Section.BASE
            assert app.snapshot(Operation.PLAN).settings.theme.opacity == 0.80
            assert app.query_one("#apply", Button).disabled
            service.release.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert not app.busy and app.last_code == 0

    asyncio.run(run())


def test_cancel_and_quit_wait_for_safe_boundary() -> None:
    async def run():
        service = Service()
        service.block = True
        app = app_for(service)
        async with app.run_test(size=(110, 42)) as pilot:
            await pilot.click("#apply")
            await pilot.pause()
            await pilot.click("#confirm")
            await pilot.pause()
            await app.action_quit()
            async with asyncio.timeout(5):
                while app.busy:
                    await asyncio.sleep(0.01)
            assert app.quit_pending and app.last_code == 3

    asyncio.run(run())


def test_confirmation_cancel_never_reaches_mutation() -> None:
    async def run():
        service = Service()
        app = app_for(service)
        async with app.run_test(size=(110, 42)) as pilot:
            await pilot.click("#apply")
            await pilot.pause()
            await pilot.press("escape")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.last_code == 2

    asyncio.run(run())


def test_failed_worker_keeps_diagnostics_and_navigation_available() -> None:
    async def run():
        service = Service()
        service.code = 5
        app = app_for(service)
        async with app.run_test(size=(110, 42)) as pilot:
            await pilot.click("#plan")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.last_code == 5 and not app.busy
            await pilot.press("ctrl+2")
            assert app.query_one("#sections", TabbedContent).active == "workspace"

    asyncio.run(run())


def test_same_form_intentions_survive_repeated_navigation() -> None:
    async def run():
        app = app_for(Service())
        async with app.run_test(size=(110, 42)) as pilot:
            app.query_one("#hyprland", Checkbox).value = True
            await pilot.press("ctrl+2")
            app.query_one("#firefox", Checkbox).value = True
            await pilot.press("ctrl+1")
            await pilot.press("ctrl+2")
            snapshot = app.snapshot(Operation.PLAN)
            assert snapshot.system.hyprland and "firefox" in snapshot.settings.apps

    asyncio.run(run())


def test_action_buttons_fit_a_standard_terminal() -> None:
    async def run():
        app = app_for(Service())
        async with app.run_test(size=(80, 25)) as pilot:
            await pilot.pause()
            for name in ("plan", "apply", "stop", "save", "tools", "quit"):
                button = app.query_one("#" + name, Button)
                assert button.region.x >= 0 and button.region.right <= 80
                assert button.region.y >= 0 and button.region.bottom <= 25

    asyncio.run(run())


def test_recovery_form_previews_and_preserves_conflicting_edits(tmp_path, monkeypatch) -> None:
    from textual.widgets import Select

    from setup_system.infrastructure.managed_files import apply_files
    from setup_system.tui.tools import ToolsScreen

    home = tmp_path / "private home"
    home.mkdir()
    for key, path in (
        ("HOME", home),
        ("XDG_STATE_HOME", home / "state"),
        ("XDG_CONFIG_HOME", home / "config"),
        ("XDG_CACHE_HOME", home / "cache"),
    ):
        monkeypatch.setenv(key, str(path))
    target = home / "config/created.txt"
    operation = apply_files({target: b"managed"})
    target.write_bytes(b"later personal edit")

    async def run():
        app = app_for(Service())
        async with app.run_test(size=(110, 48)) as pilot:
            await pilot.click("#tools")
            await pilot.pause()
            assert isinstance(app.screen, ToolsScreen)
            app.screen.query_one(TabbedContent).active = "recovery-pane"
            app.screen.query_one("#recovery-operation", Select).value = operation.name
            await pilot.pause()
            await pilot.click("#restore-preview")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert any("conflict" in line.lower() for line in app.output_lines)

            await pilot.click("#tools")
            await pilot.pause()
            app.screen.query_one(TabbedContent).active = "recovery-pane"
            app.screen.query_one("#recovery-operation", Select).value = operation.name
            await pilot.pause()
            await pilot.click("#restore")
            await pilot.pause()
            assert isinstance(app.screen, Prompt)
            await pilot.click("#confirm")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.last_code != 0 and target.read_bytes() == b"later personal edit"

    asyncio.run(run())


def test_project_git_and_template_forms_produce_only_their_required_inputs(tmp_path) -> None:
    from textual.widgets import Select

    from setup_system.domain.tool_requests import (
        CacheTemplate,
        ConfigureGit,
        InspectGit,
        WriteTemplate,
        answer_values,
    )
    from setup_system.tui.tools import ToolsScreen

    async def run():
        app = app_for(Service())
        async with app.run_test(size=(110, 48)) as pilot:
            await pilot.click("#tools")
            await pilot.pause()
            form = app.screen
            assert isinstance(form, ToolsScreen)
            form.query_one("#git-directory", Input).value = str(tmp_path)
            form.query_one("#git-profile", Input).value = "work"
            form.query_one("#owner-directory", Input).value = str(tmp_path.parent)
            assert form.request_for("git_inspect") == InspectGit(destination=tmp_path)
            assert form.request_for("git_configure") == ConfigureGit(
                name="work", destination=tmp_path, owner_directory=tmp_path.parent
            )
            form.query_one("#template", Select).value = "python"
            await pilot.pause()
            cache = form.request_for("template_cache")
            assert isinstance(cache, CacheTemplate) and cache.name == "python"
            with pytest.raises(ValueError, match="directory"):
                form.request_for("create_template")
            form.query_one("#template-directory", Input).value = str(tmp_path / "new")
            form.query_one("#project-name", Input).value = "sample"
            form.query_one("#python-profile", Select).value = "service"
            request = form.request_for("create_template")
            assert isinstance(request, WriteTemplate)
            assert answer_values(request.answers) == {"projectName": "sample", "profile": "service"}

    asyncio.run(run())


def test_external_intention_edit_refuses_apply_before_confirmation() -> None:
    async def run():
        service = Service()
        app = app_for(service)
        async with app.run_test(size=(110, 42)) as pilot:
            assert app.revisions_loaded
            draft = Path.home() / "config/setup-system/system-intentions.toml"
            draft.parent.mkdir(parents=True)
            draft.write_text('timezone = "Etc/UTC"\n')
            await pilot.click("#apply")
            await pilot.pause()
            assert not app.busy and not service.requests and not isinstance(app.screen, Prompt)
            assert draft.read_text() == 'timezone = "Etc/UTC"\n'

    asyncio.run(run())
