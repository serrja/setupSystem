"""Only the latest bounded choice request can change the active form."""

import asyncio

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Select

from setup_system.ui.schema_fields import SchemaForm


class ChoicesApp(App):
    def __init__(self, provider):
        super().__init__()
        self.form = SchemaForm(
            {"properties": {"host": {"type": "string", "ui": {"choice_provider": "hosts"}}}},
            {},
            provider,
        )

    def compose(self) -> ComposeResult:
        yield self.form


def test_stale_completion_cannot_overwrite_latest_query():
    async def exercise():
        started, cancelled = asyncio.Event(), asyncio.Event()
        seen = []

        async def provider(name, text):
            seen.append(text)
            if text == "old":
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return ["one", "two"] if text else []

        app = ChoicesApp(provider)
        async with app.run_test():
            editor = app.form.fields[0]
            editor.refresh_choices("old")
            await asyncio.wait_for(started.wait(), 2)
            latest = editor.refresh_choices("new")
            await asyncio.wait_for(cancelled.wait(), 1)
            await latest.wait()
            assert seen[-1] == "new"
            assert isinstance(editor.control, Select)
            editor.control.value = "two"
            assert app.form.values() == {"host": "two"}

    asyncio.run(exercise())


def test_fast_edits_debounce_and_all_choices_stay_bounded():
    async def exercise():
        seen = []

        async def provider(name, text):
            seen.append(text)
            return [str(i) for i in range(400)]

        app = ChoicesApp(provider)
        async with app.run_test():
            editor = app.form.fields[0]
            for text in ("o", "or", "origin"):
                latest = editor.refresh_choices(text)
            await latest.wait()
            assert seen == ["origin"]
            editor.control.value = "127"
            with pytest.raises(Exception, match="Illegal select value"):
                editor.control.value = "128"

    asyncio.run(exercise())
