"""Exercise the public Textual controls with an isolated workstation and synthetic resources."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from test_extensions import descriptor as manifest_data
from test_native_ui import select, workstation
from textual.widgets import Input, Select, Static, TabbedContent

from setup_system import app as shell
from setup_system import extensions
from setup_system.domain.receipts import digest
from setup_system.infrastructure.managed_files import apply_files
from setup_system.journal import Event
from setup_system.ui.help import Help
from setup_system.ui.plugin_dialogs import Choose, Confirm, Prompt
from setup_system_sdk.protocol import Reply


async def click(pilot, button):
    assert await pilot.click(button)
    await pilot.pause()


async def choose(app, pilot, value):
    assert isinstance(app.screen, Choose)
    app.screen.query_one(Select).value = value
    await click(pilot, "#submit")


def test_profile_preview_then_cancel_and_explicit_apply(tmp_path, monkeypatch):
    app, seen = workstation(tmp_path, monkeypatch)
    app.root = tmp_path / "package"
    profiles = app.root / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "studio.toml").write_text(
        'name = "Studio"\ndescription = "Prepare the fixture workstation."\n'
        '[[actions]]\nplugin = "alpha"\naction = "configure"\nprofile = "studio"\n'
        '[[actions]]\nplugin = "beta"\naction = "configure"\n'
    )

    async def exercise():
        async with app.run_test(size=(135, 52)) as pilot:
            page = await select(app, pilot, "alpha")
            tabs = page.query_one(TabbedContent)
            await click(pilot, tabs.get_tab("settings"))
            await click(pilot, "#new-profile")
            assert isinstance(app.screen, Prompt)
            app.screen.query_one(Input).value = "studio"
            await pilot.press("enter")
            await pilot.pause()
            assert page.profile == "studio"
            title = next(field for field in page.settings_form.fields if field.key == "title")
            title.control.value = "Studio Окно"
            await click(pilot, "#save")
            await app.workers.wait_for_complete()
            assert (await app.store.read("alpha", "studio")).values == {"title": "Studio Окно"}
            assert not seen

            for approve in (False, True):
                await click(pilot, "#workstation-profiles")
                await choose(app, pilot, "studio")
                assert isinstance(app.screen, Confirm)
                assert "alpha · configure · ready" in app.screen.text
                assert "beta · configure · ready" in app.screen.text
                assert "Studio Окно" in app.screen.text
                await click(pilot, "#approve" if approve else "#back")
                await app.workers.wait_for_complete()
                if not approve:
                    assert not seen
            assert [values["title"] for values in seen] == ["Studio Окно", "Example"]
            assert "beta · complete" in str(app.query_one("#feedback", Static).content)

    asyncio.run(exercise())


def test_offline_help_and_history_use_the_selected_scope(tmp_path, monkeypatch):
    app, seen = workstation(tmp_path, monkeypatch)
    app.root = tmp_path / "package"
    guides = app.root / "docs"
    guides.mkdir(parents=True)
    (guides / "configuration.md").write_text("# Configuration\n\nSynthetic configuration.\n")
    (guides / "recovery.md").write_text("# Recovery\n\nUse the reviewed backup.\n")
    (tmp_path / "module/settings_reference.md").write_text("Generated settings reference.")

    async def exercise():
        for plugin, action in (("alpha", "observe-alpha"), ("beta", "observe-beta")):
            await app.manager.journal.emit(
                Event(
                    plugin_id=plugin,
                    action_id=action,
                    operation_id=uuid4(),
                    phase="verified",
                    message_code="program_observed",
                )
            )
        async with app.run_test(size=(135, 52)) as pilot:
            await pilot.press("f1")
            await pilot.pause()
            assert isinstance(app.screen, Help)
            app.screen.query_one("#help-topic", Select).value = "recovery"
            await pilot.pause()
            await app.workers.wait_for_complete()
            heading = app.screen.query_one("MarkdownH1")
            assert "Recovery" in str(heading.render())
            await click(pilot, "#help-close")
            page = await select(app, pilot, "alpha")
            tabs = page.query_one(TabbedContent)
            await click(pilot, tabs.get_tab("help"))
            assert "# Fixture guide" in str(page.query_one("#article", Static).content)
            await click(pilot, "#field-reference")
            assert "Generated settings reference" in str(page.reference.content)
            await click(pilot, tabs.get_tab("history"))
            await click(pilot, "#refresh-history")
            text = str(page.query_one("#events", Static).content)
            assert "observe-alpha" in text and "observe-beta" not in text
            await click(pilot, "#global-history")
            assert isinstance(app.screen, Confirm)
            assert all(action in app.screen.text for action in ("observe-alpha", "observe-beta"))
            await click(pilot, "#approve")
            await click(pilot, "#export-history")
            await app.workers.wait_for_complete()
            (exported,) = (app.manager.journal.directory.parent / "reports").glob("*.jsonl")
            assert {
                json.loads(line)["plugin_id"] for line in exported.read_text().splitlines()
            } == {"alpha", "beta"}
            assert exported.stat().st_mode & 0o777 == 0o600 and not seen

    asyncio.run(exercise())


def test_external_preview_and_refused_digest_do_not_activate(tmp_path, monkeypatch):
    app, seen = workstation(tmp_path, monkeypatch)
    policy = extensions.TrustPolicy(hosts={"plugins.example.test"})
    monkeypatch.setattr(shell, "source_policy", lambda: policy)
    fields = manifest_data("extra", permissions=["account-keys"])
    name = "fixture-1.0-py3-none-any.whl"
    raw = (
        extensions.ReleaseDocument.model_validate(
            {"releases": [{"manifest": fields, "wheels": [{"name": name, "sha256": "e" * 64}]}]}
        )
        .model_dump_json()
        .encode()
    )
    url = "https://plugins.example.test/release.json"

    async def preview(link, approved):
        assert approved == policy
        if link == url:
            return extensions.Release(raw, url)
        # Invalid schemes are rejected by the real source check before any request.
        return await extensions.preview_link(link, approved)

    monkeypatch.setattr(shell, "preview_link", preview)
    installs = []

    async def install(*args, **kwargs):
        installs.append(args)

    monkeypatch.setattr(shell, "install_release", install)

    async def exercise():
        async with app.run_test(size=(135, 52)) as pilot:
            for answer, approve in (("file:///tmp/fixture", False), (url, False), (url, True)):
                await click(pilot, "#add-link")
                assert isinstance(app.screen, Prompt)
                app.screen.query_one(Input).value = answer
                await pilot.press("enter")
                await pilot.pause()
                if answer != url:
                    await app.workers.wait_for_complete()
                    assert "allowed origin" in str(app.query_one("#feedback", Static).content)
                    assert "alpha" in app.entries
                    continue
                assert isinstance(app.screen, Confirm)
                assert (
                    "Author: Fixture" in app.screen.text
                    and "Permissions: account-keys" in app.screen.text
                )
                assert url in app.screen.text and extensions.Release(raw).digest in app.screen.text
                if approve:
                    await click(pilot, "#approve")
                    assert isinstance(app.screen, Prompt)
                    app.screen.query_one(Input).value = "0" * 64
                    await pilot.press("enter")
                else:
                    await click(pilot, "#back")
                await app.workers.wait_for_complete()
                assert not installs and not seen
                assert not app.manager.env_root.exists()
            assert "allowed origin" in str(app.query_one("#feedback", Static).content)

    asyncio.run(exercise())


def test_previous_settings_import_requires_consent_and_preserves_backup(tmp_path, monkeypatch):
    from setup_plugin_hyprland import Settings as Compositor
    from setup_plugin_waybar import Settings as Panel

    from setup_system.extensions import Activation

    app, seen = workstation(tmp_path, monkeypatch)
    folder = app.store.directory
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    old = folder / "workspace.toml"
    old.write_text("schema_version = 1\n[theme]\neffects = true\nrounding = 7\n")
    old.chmod(0o600)
    original_bytes = old.read_bytes()
    current = app.manager.activation

    async def resolve(plugin):
        sample = await current("alpha")
        return Activation(
            sample.python,
            sample.manifest.model_copy(update={"id": plugin}),
            sample.digest,
            sample.resources,
        )

    checked = []

    async def validate(state, request, options):
        assert request.method == "validate"
        model = {"hyprland": Compositor, "waybar": Panel}[state.manifest.id]
        checked.append(state.manifest.id)
        return Reply(settings=model.model_validate(request.overrides).model_dump())

    monkeypatch.setattr(app.manager, "activation", resolve)
    monkeypatch.setattr(shell, "query", validate)

    async def exercise():
        async with app.run_test(size=(135, 52)) as pilot:
            for consent in (False, True):
                await click(pilot, "#legacy")
                await choose(app, pilot, "workspace.toml")
                assert isinstance(app.screen, Confirm)
                assert "hyprland" in app.screen.text and "waybar" in app.screen.text
                await click(pilot, "#approve" if consent else "#back")
                await app.workers.wait_for_complete()
                observed = (await app.store.read("hyprland", "imported")).values
                if consent:
                    assert observed == {"effects": True, "rounding": 7}
                else:
                    assert observed == {} and not checked
            assert (
                checked == ["hyprland", "waybar"]
                and old.read_bytes() == original_bytes
                and not seen
            )
            target = Path.home() / "note"
            target.write_text("existing")
            receipt = apply_files({target: b"new"}, expected={target: digest(b"existing")}).name
            await click(pilot, "#old-restore")
            await choose(app, pilot, receipt)
            assert isinstance(app.screen, Confirm) and str(target) in app.screen.text
            await pilot.press("escape")
            await app.workers.wait_for_complete()
            assert target.read_text() == "new" and old.read_bytes() == original_bytes
            assert not seen

    asyncio.run(exercise())
