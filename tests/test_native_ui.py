"""Exercise the public Textual catalog, schema editors and operation confirmation."""

import asyncio
import sys
from pathlib import Path
from typing import Literal

from pydantic import Field
from textual.widgets import Button, Input, OptionList, Select, Switch, TabbedContent

from setup_system import app as shell
from setup_system import plugin_manager
from setup_system.configuration import Settings
from setup_system.extensions import Activation, CatalogEntry
from setup_system.plugin_manager import PluginManager
from setup_system.ui import plugin_page
from setup_system.ui.plugin_dialogs import Confirm
from setup_system.ui.plugin_page import PluginPage
from setup_system_sdk import Action, Manifest, Outcome, Plan, PluginSettings, Result
from setup_system_sdk.protocol import Reply


class Preferences(PluginSettings):
    tone: Literal["bright", "quiet"] = "quiet"
    enabled: bool = False
    count: int = Field(default=2, ge=1, le=4)
    title: str = Field(default="Example", max_length=32)


def workstation(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    for var in ("STATE", "DATA", "CONFIG", "CACHE"):
        monkeypatch.setenv(f"XDG_{var}_HOME", str(home / var.lower()))
    folder = tmp_path / "module"
    folder.mkdir()
    (folder / "help.md").write_text("# Fixture guide")
    manager = PluginManager(home / "data/extensions", home / "state/setup-system")

    def entry(name):
        return CatalogEntry(
            Manifest(
                id=name,
                name=name.title(),
                author="Tests",
                category="tools",
                kind="application",
                sdk=">=1,<2",
                entry_point="demo:Backend",
                version="1.0",
                source="https://example.invalid",
                permissions=[],
                capabilities=[],
                dependencies=[],
                actions=[Action(id="configure", label="Configure", target="configure")],
                settings_schema=Preferences.model_json_schema(),
                input_schemas={"configure": {"type": "object", "properties": {}}},
            ),
            source=folder,
        )

    entries = [entry("alpha"), entry("beta")]
    monkeypatch.setattr(shell, "builtins", lambda root: tuple(entries))
    monkeypatch.setattr(shell, "installed", lambda root: ())
    monkeypatch.setattr(shell, "interactive_logins", lambda: ["fixture"])

    async def activate(plugin):
        item = next(e for e in entries if e.manifest.id == plugin)
        return Activation(Path(sys.executable), item.manifest, "a" * 64, folder)

    monkeypatch.setattr(manager, "activation", activate)
    seen = []

    async def query(state, req, options):
        values = Preferences.resolve(
            profile=req.profile, saved=req.saved, overrides=req.overrides
        ).model_dump()
        if req.method == "validate":
            return Reply(settings=values)
        if req.method == "plan":
            return Reply(settings=values, plan=Plan(summary=values["title"]))
        if req.method == "verify":
            return Reply(result=Result(outcome=Outcome.COMPLETE, message="Verified"))
        return Reply(result=Result(outcome=Outcome.NO_CHANGES, message="Present"))

    async def native(state, snapshot, password):
        seen.append(snapshot.mutation.settings.copy())
        return Result(outcome=Outcome.CHANGED, message="Applied")

    monkeypatch.setattr(plugin_manager, "query", query)
    monkeypatch.setattr(plugin_page, "query", query)
    monkeypatch.setattr(manager.runner, "execute", native)
    return shell.SetupApp(manager=manager, store=Settings(home, home / "config")), seen


async def select(app, pilot, plugin):
    listing = app.query_one("#catalog", OptionList)
    listing.highlighted = listing.get_option_index(plugin)
    listing.focus()
    await pilot.press("enter")
    await pilot.pause()
    return app.query_one("#plugin-" + plugin, PluginPage)


def test_schema_fields_save_and_navigation_are_read_only(tmp_path, monkeypatch):
    app, seen = workstation(tmp_path, monkeypatch)

    async def exercise():
        async with app.run_test(size=(95, 32)) as pilot:
            page = await select(app, pilot, "alpha")
            assert page.settings_form is not None
            editors = {f.key: f.control for f in page.settings_form.fields}
            assert isinstance(editors["enabled"], Switch) and isinstance(editors["tone"], Select)
            assert isinstance(editors["count"], Input)
            editors["title"].value = "Équipe de Sylvia"
            await page._command("save")
            assert (await app.store.read("alpha")).values == {"title": "Équipe de Sylvia"}
            await select(app, pilot, "beta")
            assert await select(app, pilot, "alpha") is page
            assert editors["title"].value == "Équipe de Sylvia" and not seen
            assert app.manager.journal.directory.exists() is False

    asyncio.run(exercise())


def test_preview_consent_and_apply_use_same_snapshot(tmp_path, monkeypatch):
    app, seen = workstation(tmp_path, monkeypatch)

    async def exercise():
        async with app.run_test(size=(100, 38)) as pilot:
            page = await select(app, pilot, "alpha")
            page.show_action("configure")
            await pilot.pause()
            await page._command("preview")
            assert not seen and page.snapshot.mutation.plan.summary == "Example"
            field = next(f for f in page.settings_form.fields if f.key == "title")
            field.control.value = "future draft"
            page.query_one("#apply", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, Confirm)
            app.screen.query_one("#back", Button).press()
            await pilot.pause()
            assert not seen
            field.control.value = "Reviewed"
            await page._command("preview")
            field.control.value = "later"
            page.query_one("#apply", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, Confirm) and "Reviewed" in app.screen.text
            app.screen.query_one("#approve", Button).press()
            await app.workers.wait_for_complete()
            assert [v["title"] for v in seen] == ["Reviewed"]
            assert page.query_one(TabbedContent).active == "actions"

    asyncio.run(exercise())
