"""Check responsiveness while another process holds the settings lock."""

import asyncio
import fcntl
import json
import os
import threading
from pathlib import Path

import pytest
from test_native_ui import Tasks, app_for, idle
from textual import events
from textual.widgets import Button, Input, Static, TabbedContent

from setup_system.app import SetupApp
from setup_system.models import PlanSnapshot
from setup_system.runner import AsyncMakeRunner, BecomePassword, Target
from setup_system.settings import Settings
from setup_system.ui.dialogs import Consent, Password


def test_save_waiting_for_directory_lock_keeps_ui_responsive(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = Settings(tmp_path, tmp_path / ".config")
        settings.directory.parent.mkdir(mode=0o700)
        settings.directory.mkdir(mode=0o700)
        app = SetupApp(settings=settings)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert "base" in app.loaded
            fd = os.open(settings.directory, os.O_RDONLY | os.O_DIRECTORY)
            held = threading.Event()
            release = threading.Event()
            expired = threading.Event()

            def lock() -> None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                    held.set()
                    if not release.wait(3):
                        expired.set()
                finally:
                    os.close(fd)

            owner = threading.Thread(target=lock)
            owner.start()
            await asyncio.to_thread(held.wait, 2)
            app.query_one("#save", Button).press()
            try:
                await asyncio.sleep(0.05)
                app.post_message(events.Key("ctrl+2", None))
                async with asyncio.timeout(2):
                    while app.query_one(TabbedContent).active != "workspace":
                        await asyncio.sleep(0.01)
                assert not expired.is_set()
            finally:
                release.set()
            await asyncio.to_thread(owner.join)
            await app.workers.wait_for_complete()
            assert (settings.directory / "base.toml").is_file()

    asyncio.run(scenario())


def test_cancel_during_publication_waits_for_the_filesystem_transaction(
    tmp_path: Path, monkeypatch
) -> None:
    async def scenario() -> None:
        settings = Settings(tmp_path, tmp_path / ".config")
        before = await settings.read("base")
        entered, release = threading.Event(), threading.Event()
        save = settings.save

        def delayed(*args):
            entered.set()
            assert release.wait(5)
            return save(*args)

        monkeypatch.setattr(settings, "save", delayed)
        writer = asyncio.create_task(settings.publish("base", before.options, before.revision))
        assert await asyncio.to_thread(entered.wait, 2)
        try:
            writer.cancel()
            await asyncio.sleep(0)
            assert not writer.done()
        finally:
            release.set()
        try:
            await writer
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("Expected cancellation after the safe write")
        assert (await settings.read("base")).revision != before.revision

    asyncio.run(scenario())


async def wait_until(check) -> None:
    async with asyncio.timeout(2):
        while not check():
            await asyncio.sleep(0.01)


def hold_reads(monkeypatch, settings):
    entered, release = asyncio.Event(), asyncio.Event()
    original = settings.read

    async def delayed(section):
        entered.set()
        await release.wait()
        return await original(section)

    monkeypatch.setattr(settings, "read", delayed)
    return entered, release


@pytest.mark.parametrize("stage", ["mount", "reload", "plan", "apply", "confirmed", "authorized"])
def test_navigation_during_settings_read(tmp_path, monkeypatch, stage):
    async def scenario():
        tasks = Tasks()
        app = app_for(tmp_path, tasks)

        async def navigate(entered, release):
            try:
                await asyncio.wait_for(entered.wait(), 5)
                if stage == "plan":
                    app.query_one("#timezone", Input).value = "Asia/Tokyo"
                app.post_message(events.Key("ctrl+2", None))
                await wait_until(lambda: app.section == "workspace")
            finally:
                release.set()

        if stage == "mount":
            entered, release = hold_reads(monkeypatch, app.settings)
            # Observe navigation even if a regression blocks entering run_test itself.
            async with asyncio.TaskGroup() as group:
                probe = group.create_task(navigate(entered, release))
                async with app.run_test(size=(110, 42)):
                    await probe
            return

        async with app.run_test(size=(110, 42)) as pilot:
            await app.workers.wait_for_complete()
            app.query_one("#timezone", Input).value = "UTC"
            if stage in {"apply", "confirmed", "authorized"}:
                await pilot.click("#plan")
                await idle(app, pilot)
                assert app.pending is not None
            if stage in {"confirmed", "authorized"}:
                await pilot.click("#apply")
                await wait_until(
                    lambda: isinstance(app.screen, Consent) and app.screen.query("#confirm")
                )
            if stage == "authorized":
                await pilot.click("#confirm")
                await wait_until(
                    lambda: isinstance(app.screen, Password) and app.screen.query("#password")
                )
                app.screen.query_one("#password", Input).value = "synthetic fixture"

            entered, release = hold_reads(monkeypatch, app.settings)
            trigger = {"confirmed": "confirm", "authorized": "authorize"}.get(stage, stage)
            app.screen.query_one("#" + trigger, Button).press()
            await navigate(entered, release)

            if stage == "plan":
                await idle(app, pilot)
                assert tasks.calls[-1][1]["settings"]["timezone"] == "UTC"
            elif stage == "apply":
                await wait_until(lambda: isinstance(app.screen, Consent))
                await pilot.press("escape")
            elif stage == "confirmed":
                await wait_until(lambda: isinstance(app.screen, Password))
                await pilot.press("escape")
            elif stage == "authorized":
                await idle(app, pilot)
                assert tasks.calls[-1][0] == Target.BASE_APPLY

    asyncio.run(scenario())


def test_password_response_is_bound_to_the_plan_on_the_consent_screen(tmp_path):
    async def scenario():
        tasks = Tasks()
        app = app_for(tmp_path, tasks)
        async with app.run_test(size=(110, 42)) as pilot:
            await pilot.click("#plan")
            await idle(app, pilot)
            await pilot.click("#apply")
            await pilot.click("#confirm")
            assert isinstance(app.screen, Password)
            reviewed = app.pending
            replacement = PlanSnapshot.capture(
                reviewed.task,
                {"timezone": "Asia/Tokyo"},
                json.loads(reviewed.expected_json),
                reviewed.revision,
            )
            app.pending = replacement
            secret = BecomePassword("synthetic fixture")
            app.screen.dismiss(secret)
            await idle(app, pilot)
            with pytest.raises(RuntimeError, match="expired"):
                secret.take()
            assert [entry[0] for entry in tasks.calls] == [Target.BASE_PLAN]
            assert app.pending is replacement

    asyncio.run(scenario())


def test_untrusted_program_directory_is_rejected_before_the_password_prompt(tmp_path):
    class UninstalledTasks(Tasks):
        async def check_authorization(self):
            return await AsyncMakeRunner(root=tmp_path).check_authorization()

    async def scenario():
        tasks = UninstalledTasks()
        app = app_for(tmp_path, tasks)
        async with app.run_test(size=(110, 42)) as pilot:
            await pilot.click("#plan")
            await idle(app, pilot)
            await pilot.click("#apply")
            assert isinstance(app.screen, Consent)
            await pilot.click("#confirm")
            await idle(app, pilot)
            assert not isinstance(app.screen, Password)
            assert "installed Debian package" in str(app.query_one("#feedback", Static).render())
            assert [call[0] for call in tasks.calls] == [Target.BASE_PLAN]

    asyncio.run(scenario())


def test_cancel_while_authorization_is_being_rechecked_discards_password(tmp_path, monkeypatch):
    async def scenario():
        tasks = Tasks()
        app = app_for(tmp_path, tasks)
        async with app.run_test(size=(110, 42)) as pilot:
            await pilot.click("#plan")
            await idle(app, pilot)
            await pilot.click("#apply")
            await pilot.click("#confirm")
            assert isinstance(app.screen, Password)
            entered, release = hold_reads(monkeypatch, app.settings)
            secret = BecomePassword("synthetic fixture")
            app.screen.dismiss(secret)
            try:
                await asyncio.wait_for(entered.wait(), 2)
                await pilot.click("#cancel")
                assert app.pending is None
            finally:
                release.set()
            await app.workers.wait_for_complete()
            with pytest.raises(RuntimeError, match="expired"):
                secret.take()
            assert [call[0] for call in tasks.calls] == [Target.BASE_PLAN]

    asyncio.run(scenario())


def test_reload_can_finish_behind_the_offline_help_screen(tmp_path, monkeypatch):
    async def scenario():
        from setup_system.ui.help import Help

        app = app_for(tmp_path, Tasks())
        async with app.run_test(size=(110, 42)) as pilot:
            await app.workers.wait_for_complete()
            entered, release = hold_reads(monkeypatch, app.settings)
            app.query_one("#reload", Button).press()
            try:
                await asyncio.wait_for(entered.wait(), 2)
                await pilot.press("f1")
                assert isinstance(app.screen, Help)
            finally:
                release.set()
            await app.workers.wait_for_complete()
            assert isinstance(app.screen, Help) and "base" in app.loaded
            await pilot.press("escape")
            assert "Reloaded base" in str(app.query_one("#feedback", Static).render())

    asyncio.run(scenario())
