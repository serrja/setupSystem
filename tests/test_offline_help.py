"""Read packaged help through the normal keyboard entry point, from any directory."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from textual.widgets import Markdown, Select, Static

from setup_system.app import SetupApp
from setup_system.ui.help import Help


def test_help_opens_local_recovery_and_returns_to_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))

    async def exercise():
        app = SetupApp()
        async with app.run_test() as pilot:
            await pilot.press("ctrl+2", "f1")
            assert isinstance(app.screen, Help)
            app.screen.query_one(Select).value = "recovery"
            await pilot.pause()
            async with asyncio.timeout(5):
                while not any(
                    "Recovery" in str(h.render())
                    for h in app.screen.query("MarkdownH1").results(Static)
                ):
                    await pilot.pause()
            await pilot.press("escape")
            assert app.section == "workspace"
            assert not list(tmp_path.iterdir())

    asyncio.run(exercise())


@pytest.mark.parametrize("ending", ["dismiss", "shutdown"])
def test_delayed_help_read_cannot_update_a_closed_guide(tmp_path, monkeypatch, ending):
    monkeypatch.setenv("HOME", str(tmp_path))

    async def exercise():
        app = SetupApp()
        reading, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        updates = []

        class SlowGuide:
            async def read(self):
                reading.set()
                try:
                    await release.wait()
                    if ending == "shutdown":
                        while app.is_running:
                            await asyncio.sleep(0)
                    return "# Delayed fixture"
                finally:
                    finished.set()

        @asynccontextmanager
        async def open_guide(*args, **kwargs):
            yield SlowGuide()

        monkeypatch.setattr("setup_system.ui.help.aiofiles.open", open_guide)
        async with app.run_test() as pilot:
            app.action_help()
            await asyncio.wait_for(reading.wait(), 2)
            guide = app.screen
            markdown = guide.query_one(Markdown)
            update = markdown.update

            def record(*args, **kwargs):
                updates.append((guide.is_active, app.is_running))
                return update(*args, **kwargs)

            monkeypatch.setattr(markdown, "update", record)
            try:
                if ending == "dismiss":
                    await asyncio.wait_for(pilot.press("escape"), 2)
                    assert app.screen is not guide
            finally:
                release.set()
        assert finished.is_set() and not updates

    asyncio.run(exercise())
