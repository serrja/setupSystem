"""Inspect the installed catalogue, open a plugin page and exercise offline file recovery."""

import asyncio
from pathlib import Path

from textual.widgets import Input, OptionList

from setup_system.app import SetupApp
from setup_system.application.recovery import recover
from setup_system.infrastructure import receipt_repository
from setup_system.infrastructure.managed_files import apply_files
from setup_system.infrastructure.restore_files import LocalRecoveryFiles


async def main() -> None:
    app = SetupApp()
    async with app.run_test(size=(110, 45)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "git" in app.entries and "plymouth" in app.entries
        assert not any(item.reason for item in app.entries.values())
        await pilot.press("ctrl+f")
        search = app.query_one("#search", Input)
        assert search.has_focus
        search.value = "git"
        await pilot.pause()
        catalog = app.query_one("#catalog", OptionList)
        assert catalog.option_count == 1
        catalog.focus()
        catalog.highlighted = 0
        await pilot.press("enter")
        await pilot.pause()
        assert "git" in app.opened
    target = Path.home() / "config with spaces/example.ini"
    operation = apply_files({target: b"installed configuration"})
    assert target.read_bytes() == b"installed configuration"
    recover(operation, apply=True, journal=receipt_repository, files=LocalRecoveryFiles())
    assert not target.exists()


if __name__ == "__main__":
    asyncio.run(main())
