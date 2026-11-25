"""Exercise the public schema form with plugin-owned Pydantic models and synthetic references."""

import asyncio
from typing import Literal
from uuid import UUID

import pytest
from pydantic import Field, ValidationError
from test_native_ui import select, workstation
from textual.app import App, ComposeResult
from textual.widgets import Button, Input, Select, SelectionList, TabbedContent

from setup_system import app as shell
from setup_system import plugin_manager
from setup_system.ui import plugin_page
from setup_system.ui.plugin_dialogs import SecretPrompt
from setup_system.ui.schema_fields import SchemaForm
from setup_system_sdk import EnvironmentValue, Inputs, Plan
from setup_system_sdk.protocol import Reply
from setup_system_sdk.secrets import SecretUnavailableError


class Variable(EnvironmentValue):
    name: Literal["FORMAT"] = "FORMAT"
    value: str = Field(default="", max_length=24)


class Target(Inputs):
    name: str = Field(default="Local", max_length=20)
    scale: float = Field(
        default=1,
        ge=0.5,
        le=2.0,
        title="Output scale",
        json_schema_extra={"ui": {"units": "times", "order": 2}},
    )


class Command(Inputs):
    mode: Literal["manual", "stored"] = "manual"
    reference: UUID | None = Field(
        default=None,
        json_schema_extra={
            "ui": {"widget": "secret_reference", "secret": True, "visible_when": {"mode": "stored"}}
        },
    )
    flags: list[Literal["verbose", "dry-run"]] = Field(default=[])
    environment: list[Variable] = Field(default=[], max_length=2)


class Layout(Inputs):
    monitors: list[Target] = Field(min_length=1, max_length=2)
    locked: bool = Field(default=True, json_schema_extra={"ui": {"readonly": True}})


class FormApp(App):
    def __init__(self, schema, data, provider=None):
        super().__init__()
        self.form = SchemaForm(schema, data, provider)

    def compose(self) -> ComposeResult:
        yield self.form


def test_nested_rows_enforce_bounds_without_translating_typed_values():
    app = FormApp(Layout.model_json_schema(), {"monitors": [{"name": "Левый", "scale": 1.25}]})

    async def exercise():
        async with app.run_test(size=(100, 48)) as pilot:
            monitors, locked = app.form.fields
            assert locked.control.disabled
            first = monitors.fields[0]
            assert first.fields[0].control.value == "Левый"
            first.fields[1].control.value = "2.5"
            with pytest.raises(ValueError, match="outside its permitted range"):
                app.form.values()
            first.fields[1].control.value = "0.75"
            add = monitors.query_one(".add", Button)
            for _ in range(2):
                assert await pilot.click(add)
                await pilot.pause()
            assert len(monitors.fields) == 2
            row = monitors.fields[1]
            row.fields[0].control.value = "Second display"
            row.fields[1].control.value = "2.0"
            rendered = Layout.model_validate(app.form.values())
            assert [(item.name, item.scale) for item in rendered.monitors] == [
                ("Левый", 0.75),
                ("Second display", 2),
            ]
            for _ in range(2):
                assert await pilot.click(monitors.query(".remove").last())
                await pilot.pause()
            with pytest.raises(ValidationError):
                Layout.model_validate(app.form.values())

    asyncio.run(exercise())


class Contexts(Inputs):
    team: Literal["local", "shared"] = "local"
    selected: list[str] = Field(
        default=[],
        json_schema_extra={
            "ui": {
                "widget": "selection_list",
                "choice_provider": "contexts",
                "depends_on": ["team"],
            }
        },
    )
    label: str = Field(default="", json_schema_extra={"ui": {"enabled_when": {"team": "shared"}}})


def test_filtering_choices_preserves_selection_and_does_not_revive_deselected_default():
    calls = []

    async def provider(name, query):
        calls.append((name, query))
        return [s for s in ("alpha", "bravo", "charlie") if query in s]

    app = FormApp(Contexts.model_json_schema(), {"selected": ["alpha"]}, provider)

    async def exercise():
        async with app.run_test(size=(100, 36)) as pilot:
            team, choices, label = app.form.fields
            await app.workers.wait_for_complete()
            assert label.disabled
            assert isinstance(choices.control, SelectionList)
            assert app.form.values()["selected"] == ["alpha"]
            choices.control.deselect("alpha")
            query = choices.query_one(".choice-query", Input)
            query.value = "a"
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert app.form.values()["selected"] == []
            choices.control.select("bravo")
            query.value = "char"
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert "bravo" in app.form.values()["selected"]
            choices.control.select("charlie")
            assert set(app.form.values()["selected"]) == {"bravo", "charlie"}
            team.control.value = "shared"
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert not label.disabled and calls[-1] == ("contexts", "")
            assert set(app.form.values()["selected"]) == {"bravo", "charlie"}

    asyncio.run(exercise())


def test_action_visibility_and_run_parameters_produce_only_the_confirmed_mode(
    tmp_path, monkeypatch
):
    app, seen = workstation(tmp_path, monkeypatch)
    for entry in shell.builtins(app.root):
        entry.manifest.input_schemas["configure"] = Command.model_json_schema()
    captured = []

    async def plan(state, req, options):
        assert req.method == "plan"
        command = Command.model_validate(req.inputs)
        captured.append(req.inputs)
        return Reply(settings={}, plan=Plan(summary=command.mode))

    monkeypatch.setattr(plugin_manager, "query", plan)
    reference = UUID("10000000-0000-4000-8000-000000000042")
    saved = []

    async def remember(manager, plugin, value):
        assert plugin == "alpha" and value.get_secret_value() == "fixture value"
        saved.append(reference)
        return reference

    monkeypatch.setattr(plugin_page, "save_keyring_entry", remember)

    async def exercise():
        async with app.run_test(size=(135, 65)) as pilot:
            page = await select(app, pilot, "alpha")
            tabs = page.query_one(TabbedContent)
            assert await pilot.click(tabs.get_tab("actions"))
            page.query_one("#action-choice", Select).value = "configure"
            await pilot.pause()
            mode, ref = page.forms["input-0"].fields
            assert not ref.display
            mode.control.value = "stored"
            await pilot.pause()
            assert ref.display and ref.control.disabled
            assert await pilot.click(ref.query_one(".set-secret", Button))
            await pilot.pause()
            assert isinstance(app.screen, SecretPrompt)
            assert app.screen.query_one(Input).password
            app.screen.query_one(Input).value = "cancelled input"
            await pilot.press("escape")
            await app.workers.wait_for_complete()
            assert not saved and not ref.control.value
            assert await pilot.click(ref.query_one(".set-secret", Button))
            await pilot.pause()
            app.screen.query_one(Input).value = "fixture value"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            assert saved == [reference] and ref.control.value == str(reference)
            mode.control.value = "manual"
            assert await pilot.click(tabs.get_tab("parameters"))
            flags, table = page.forms["run-0"].fields
            assert isinstance(flags.control, SelectionList)
            flags.control.select("dry-run")
            assert await pilot.click(table.query_one(".add", Button))
            await pilot.pause()
            row = table.fields[0]
            value = next(f for f in row.fields if f.key == "value")
            value.control.value = "Notes Пример"
            assert await pilot.click(tabs.get_tab("actions"))
            assert await pilot.click("#preview")
            await app.workers.wait_for_complete()
            assert len(captured) == 1 and "reference" not in captured[0]
            assert captured[0]["environment"] == [
                {"name": "FORMAT", "value": "Notes Пример", "scope": "command"}
            ]
            assert captured[0]["flags"] == ["dry-run"]
            mode.control.value = "stored"
            await pilot.pause()
            assert await pilot.click("#preview")
            await app.workers.wait_for_complete()
            assert captured[1]["reference"] == str(reference) and not seen
            assert all("fixture value" not in str(snapshot) for snapshot in captured)

    asyncio.run(exercise())


def test_keyring_failure_keeps_existing_reference_and_clears_dialog():
    ref = UUID("30000000-0000-4000-8000-000000000001")
    app = FormApp(Command.model_json_schema(), {"mode": "stored", "reference": str(ref)})

    async def unavailable(secret):
        raise SecretUnavailableError("Synthetic locked keyring")

    app.form.save_secret = unavailable

    async def exercise():
        async with app.run_test(size=(110, 60)) as pilot:
            reference = next(f for f in app.form.fields if f.key == "reference")
            assert await pilot.click(reference.query_one(".set-secret", Button))
            await pilot.pause()
            app.screen.query_one(Input).value = "fixture secret"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            assert not isinstance(app.screen, SecretPrompt)
            assert app.form.values()["reference"] == str(ref)
            assert "fixture secret" not in str(app.form.values())

    asyncio.run(exercise())
