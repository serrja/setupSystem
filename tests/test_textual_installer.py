"""The catalog opens and filters without native execution or installation."""

import asyncio

from test_native_ui import select, workstation
from textual.widgets import Input, OptionList, TabbedContent


def test_category_search_help_and_profiles_preserve_drafts(tmp_path, monkeypatch):
    app, seen = workstation(tmp_path, monkeypatch)

    async def exercise():
        async with app.run_test(size=(80, 24)) as pilot:
            app.query_one("#search", Input).value = "beta"
            await pilot.pause()
            assert app.query_one("#catalog", OptionList).option_count == 1
            page = await select(app, pilot, "beta")
            await page.change_profile("work")
            title = next(f for f in page.settings_form.fields if f.key == "title")
            title.control.value = "Unsaved draft"
            await page.change_profile("default")
            await page.change_profile("work")
            assert page.settings_form.values()["title"] == "Unsaved draft"
            assert page.query_one(TabbedContent).active == "overview" and not seen
            assert not (await app.store.read("beta", "work")).values

    asyncio.run(exercise())
