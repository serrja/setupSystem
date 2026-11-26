from pathlib import Path

import pytest
from textual.widgets import OptionList

from setup_system.app import SetupApp
from setup_system.bootstrap import runtime


@pytest.mark.asyncio
async def test_empty_catalog_starts_without_providers(home: Path) -> None:
    async with runtime() as services:
        app = SetupApp(services)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one("#catalog", OptionList).option_count == 0
            assert await services.recovery.history() == []
