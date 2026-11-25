"""Validate packaged delivery and native handoffs without starting the application."""

from setup_plugin_telegram import Plugin

from setup_system_sdk import Inputs, Manifest, Outcome, PluginSettings
from setup_system_sdk.apt import Packages


def test_package_and_manual_actions_match_the_contract():
    plugin = Plugin()
    path = plugin.package_folder
    package = Packages.model_validate_json((path / "apt.json").read_bytes())
    manifest = Manifest.model_validate_json((path / "manifest.json").read_bytes())
    assert manifest.id == "telegram" and package.names == ("telegram-desktop",)
    assert {a.id for a in manifest.actions} == set(plugin.inputs)
    for action in plugin.manual:
        plan = plugin.prepare(action, PluginSettings(), Inputs())
        assert plan.outcome == Outcome.NEEDS_USER_ACTION and not plan.resources
