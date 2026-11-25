"""Exercise command IDs and ordinary CLI readback using a synthetic open vault."""

import asyncio
import subprocess

import setup_plugin_obsidian as app

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
    monkeypatch.setattr(app, "protected", lambda *a, **kw: None)
    state = {"theme": "Default", "feature": "", "running": True}

    def native(*argv, **kwargs):
        if argv[0] == "/usr/bin/pgrep":
            return (0 if state["running"] else 1), ""
        assert argv[0] == "/usr/bin/obsidian" and argv[1] == "vault=Notes & docs"
        action = argv[2]
        if action == "vault":
            return 0, str(folder)
        if action == "theme:set":
            state["theme"] = argv[3].removeprefix("name=")
        if action in {"plugin:enable", "plugin:disable"}:
            assert argv[3:] == ("id=bookmarks", "filter=core")
            state["feature"] = "bookmarks" if action == "plugin:enable" else ""
        return 0, {
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
