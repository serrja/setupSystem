"""Exercise command IDs and ordinary CLI readback using a synthetic open vault."""

import asyncio
import hashlib
import subprocess
from uuid import uuid4

import pytest
import setup_plugin_obsidian as app

from setup_system.resource_ledger import ResourceLedger
from setup_system_sdk import Mutation, Outcome


def test_theme_feature_hotkey_and_verified_launch(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    folder = tmp_path / "Notes & docs"
    folder.mkdir()
    note = folder / "personal.md"
    note.write_text("Owner's note")
    launcher = tmp_path / "obsidian.desktop"
    launcher.touch()
    monkeypatch.setattr(app, "LAUNCHER", launcher)
    monkeypatch.setattr(app, "launcher_available", lambda: True)
    cli = tmp_path / ".local/bin/obsidian"
    cli.parent.mkdir(parents=True)
    cli.write_bytes(b"official CLI fixture")
    cli.chmod(0o700)
    packaged = hashlib.md5(cli.read_bytes(), usedforsecurity=False).hexdigest()
    state = {"theme": "Default", "feature": "", "snippet": "", "running": True}

    def native(*argv, **kwargs):
        if argv[0] == "/usr/bin/pgrep":
            return (0 if state["running"] else 1), ""
        if argv[0] == "/usr/bin/dpkg-query":
            assert argv[1:] == ("--control-show", "obsidian", "md5sums")
            return 0, f"{packaged}  opt/Obsidian/obsidian-cli"
        assert argv[0] == str(cli) and argv[1] == "vault=Notes & docs"
        action = argv[2]
        if action == "vault":
            return 0, str(folder)
        if action == "theme:set":
            state["theme"] = argv[3].removeprefix("name=")
        if action in {"plugin:enable", "plugin:disable"}:
            assert argv[3:] == ("id=bookmarks", "filter=core")
            state["feature"] = "bookmarks" if action == "plugin:enable" else ""
        if action in {"snippet:enable", "snippet:disable"}:
            assert argv[3:] == ("name=Editor width",)
            state["snippet"] = "Editor width" if action == "snippet:enable" else ""
        return 0, {
            "snippets": "Editor width",
            "snippets:enabled": state["snippet"],
            "snippet:enable": "",
            "snippet:disable": "",
            "themes": "Default\nCalm",
            "theme": state["theme"],
            "plugins": "bookmarks",
            "plugins:enabled": state["feature"],
            "commands": "app:open-settings",
            "hotkey": "Ctrl+,",
            "theme:set": "",
            "plugin:enable": "",
            "plugin:disable": "",
        }[action]

    monkeypatch.setattr(app, "command", native)

    def launch(argv, **kwargs):
        assert argv[:3] == ["/usr/bin/gio", "launch", str(launcher)] and "%26" in argv[3]
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(app, "run_command", launch)
    plugin = app.Plugin()
    settings = app.Settings(vault=folder)
    for action, data in (
        ("set-theme", app.Theme(name="Calm")),
        ("core-feature", app.CoreFeature()),
        ("editor-snippet", app.EditorSnippet(name="Editor width")),
        ("editor-snippet", app.EditorSnippet(name="Editor width", enabled=False)),
        ("open", app.OpenVault()),
    ):
        plan = plugin.prepare(action, settings, data)
        assert plan.outcome == Outcome.READY
        result = asyncio.run(
            plugin.apply_request(
                action,
                Mutation(
                    action_id=action,
                    settings=settings.model_dump(mode="json"),
                    inputs=data.model_dump(mode="json"),
                    plan=plan,
                ),
            )
        )
        assert result.outcome == Outcome.CHANGED
        assert asyncio.run(plugin.verify(plan, settings, data)).outcome == Outcome.COMPLETE
    assert asyncio.run(plugin.choices("themes", "al", settings)) == ["Calm"]
    assert asyncio.run(plugin.choices("snippets", "wid", settings)) == ["Editor width"]
    unchanged = plugin.prepare(
        "editor-snippet", settings, app.EditorSnippet(name="Editor width", enabled=False)
    )
    assert unchanged.outcome is Outcome.NO_CHANGES
    row = unchanged.resources[0]
    assert row.identity == f"{folder}/snippet/Editor width" and row.ownership == "modified"
    assert row.before == row.after == "False" and not row.cleanup

    async def record_editor_change():
        ledger = ResourceLedger(tmp_path / "receipts" / "resources.sqlite3")
        operation = uuid4()
        data = app.EditorSnippet(name="Editor width")
        plan = await plugin.plan("editor-snippet", settings, data)
        await ledger.prepare(operation, "obsidian", "editor-snippet", "a" * 64, plan)
        native = await plugin.apply_request(
            "editor-snippet",
            Mutation(
                action_id="editor-snippet",
                settings=settings.model_dump(mode="json"),
                inputs=data.model_dump(mode="json"),
                plan=plan,
            ),
        )
        await ledger.applied(operation, observations=tuple(native.resources))
        await ledger.finish(operation, await plugin.verify(plan, settings, data))
        [held] = await ledger.holdings("obsidian")
        assert held.identity == f"{folder}/snippet/Editor width"
        assert held.before == "False" and held.after == "True" and not held.cleanup

    asyncio.run(record_editor_change())
    assert (
        plugin.prepare("editor-snippet", settings, app.EditorSnippet(name="Missing")).outcome
        is Outcome.BLOCKED
    )
    for action in ("hotkeys", "editor"):
        assert plugin.prepare(action, settings, app.Inputs()).outcome is Outcome.NEEDS_USER_ACTION
    assert (
        plugin.prepare("read-hotkey", settings, app.Hotkey(command_id="app:open-settings")).outcome
        == Outcome.NO_CHANGES
    )
    assert (
        plugin.prepare("read-hotkey", settings, app.Hotkey(command_id="unavailable")).outcome
        == Outcome.BLOCKED
    )
    state["running"] = False
    assert (
        plugin.prepare("set-theme", settings, app.Theme(name="Calm")).outcome
        == Outcome.NEEDS_USER_ACTION
    )
    assert not asyncio.run(plugin.choices("themes", "", settings))
    assert (
        asyncio.run(plugin.verify(plan, settings, app.OpenVault())).outcome
        == Outcome.NEEDS_USER_ACTION
    )
    assert note.read_text() == "Owner's note"


def test_cli_registration_requires_the_packaged_bytes_and_an_owned_executable(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    registered = tmp_path / ".local/bin/obsidian"
    registered.parent.mkdir(parents=True)
    reference = b"sample of the package CLI"
    checksum = hashlib.md5(reference, usedforsecurity=False).hexdigest()
    monkeypatch.setattr(
        app, "command", lambda *a, **kw: (0, checksum + "  opt/Obsidian/obsidian-cli")
    )
    assert app.registered_cli() is None
    registered.write_bytes(reference)
    registered.chmod(0o755)
    assert app.registered_cli() == registered
    registered.chmod(0o644)
    assert app.registered_cli() is None
    registered.chmod(0o755)
    registered.write_bytes(b"old or unrelated registration")
    assert app.registered_cli() is None
    saved = tmp_path / "previous"
    registered.rename(saved)
    registered.symlink_to(saved)
    assert app.registered_cli() is None
    with pytest.raises(ValueError, match="Enable"):
        app.native(tmp_path, "hotkeys")


def test_changed_vault_or_unconfirmed_snippet_cannot_report_a_successful_write(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = tmp_path / "Notes"
    vault.mkdir()
    custom = vault / ".custom"
    custom.mkdir()
    shortcuts = custom / "hotkeys.json"
    shortcuts.write_text("fixture preserved")
    settings = app.Settings(vault=vault)
    plugin = app.Plugin()
    data = app.EditorSnippet(name="Editor style")
    responses = {"snippets": "Editor style", "snippets:enabled": "", "snippet:enable": ""}
    monkeypatch.setattr(app, "running", lambda selected: selected == vault)
    monkeypatch.setattr(app, "native", lambda selected, action, *args: responses[action])
    plan = plugin.prepare("editor-snippet", settings, data)
    mutation = Mutation(
        action_id="editor-snippet",
        settings=settings.model_dump(mode="json"),
        inputs=data.model_dump(mode="json"),
        plan=plan,
    )
    assert asyncio.run(plugin.apply_request("editor-snippet", mutation)).outcome is Outcome.CHANGED
    assert asyncio.run(plugin.verify(plan, settings, data)).outcome is Outcome.PARTIAL
    responses["snippets"] = "Different"
    assert asyncio.run(plugin.apply_request("editor-snippet", mutation)).outcome is Outcome.BLOCKED
    assert shortcuts.read_text() == "fixture preserved"
