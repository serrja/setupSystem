"""Read packaged help through the normal keyboard entry point, from any directory."""

import asyncio

from textual.widgets import Select, Static

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
