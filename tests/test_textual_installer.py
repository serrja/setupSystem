"""Headless Textual tests preserve navigation, snapshots, consent and safe cancellation."""

import asyncio
from pathlib import Path
from threading import Event

from textual.widgets import Button, Checkbox, Input, TabbedContent

from setup_system.application.installer import InstallRequest, Operation
from setup_system.domain.settings import Section
from setup_system.tui.installer import InstallationApp, Prompt

ROOT = Path(__file__).resolve().parents[1]


class Service:
    def __init__(self):
        self.requests = []
        self.started = Event()
        self.release = Event()
        self.block = False
        self.code = 0
        self.interaction = None

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
    app = InstallationApp(ROOT, InstallRequest(Operation.PLAN), service=service)
    service.interaction = app.interaction
    return app


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
            assert snapshot.system.hyprland and snapshot.settings.firefox

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
