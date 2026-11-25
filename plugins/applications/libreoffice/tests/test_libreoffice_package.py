"""Validate packaged delivery and native handoffs without starting the application."""

from setup_plugin_libreoffice import Plugin

from setup_system_sdk import Inputs, Manifest, Outcome, PluginSettings
from setup_system_sdk.apt import Packages


def test_package_and_manual_actions_match_the_contract():
    plugin = Plugin()
    path = plugin.package_folder
    package = Packages.model_validate_json((path / "apt.json").read_bytes())
    manifest = Manifest.model_validate_json((path / "manifest.json").read_bytes())
    assert manifest.id == "libreoffice" and package.names == ("libreoffice",)
    assert {a.id for a in manifest.actions} == set(plugin.inputs)
    for action in plugin.manual:
        plan = plugin.prepare(action, PluginSettings(), Inputs())
        assert plan.outcome == Outcome.NEEDS_USER_ACTION and not plan.resources


def test_user_launch_profile_preserves_other_applications(monkeypatch, tmp_path):
    import asyncio
    import configparser
    from pathlib import Path

    import pytest
    from pydantic import ValidationError
    from setup_plugin_libreoffice import Settings

    from setup_system.application.recovery import restore
    from setup_system_sdk import Mutation, ownership_context
    from setup_system_sdk.native import LauncherProfile

    monkeypatch.setenv("HOME", str(tmp_path))
    for scope in ("DATA", "STATE", "CONFIG", "CACHE"):
        monkeypatch.setenv("XDG_" + scope + "_HOME", str(tmp_path / scope.lower()))
    data = LauncherProfile(profile_id="work", name="Работа $(touch harmless) 50%")
    with pytest.raises(ValidationError):
        LauncherProfile(profile_id="bad", name="Line\nExec=anything")
    settings = Settings(component="calc")
    plugin = Plugin()
    plan = plugin.prepare("configure-launcher", settings, data)
    assert plan.outcome == Outcome.READY
    result = asyncio.run(
        plugin.apply_request(
            "configure-launcher",
            Mutation(
                action_id="configure-launcher",
                settings=settings.model_dump(),
                inputs=data.model_dump(),
                plan=plan,
            ),
        )
    )
    assert result.outcome == Outcome.CHANGED and result.recovery_id
    checked = asyncio.run(plugin.verify(plan, settings, data))
    assert checked.outcome == Outcome.COMPLETE
    row = checked.resources[0]
    entry = Path(row.identity)
    parsed = configparser.ConfigParser(interpolation=None)
    parsed.read(entry)
    assert parsed["Desktop Entry"]["Name"] == data.name
    assert parsed["Desktop Entry"]["Exec"] == "/usr/bin/libreoffice --calc"
    with ownership_context((row,)):
        assert plugin.prepare("configure-launcher", settings, data).outcome == Outcome.NO_CHANGES
        old = entry.read_bytes()
        entry.write_text("[Desktop Entry]\nName=Owner")
        assert plugin.prepare("configure-launcher", settings, data).outcome == Outcome.BLOCKED
        entry.write_bytes(old)
    sibling = entry.with_name("owner.desktop")
    sibling.write_text("Owner")
    restore(result.recovery_id, apply=True)
    assert sibling.read_text() == "Owner" and not entry.exists()
