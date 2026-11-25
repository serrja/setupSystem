"""Verify named profile selections without authorizing or installing any application."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from setup_system.configuration import Settings
from setup_system.profiles import preview_profile, read_profiles

ROOT = Path(__file__).resolve().parents[1]


def test_profiles_select_only_declared_actions_and_do_not_enable_a_vpn(tmp_path):
    profiles = read_profiles(ROOT)
    assert set(profiles) == {"base", "workspace"}
    for profile in profiles.values():
        for row in profile.actions:
            assert row.plugin != "happ"
            manifests = list((ROOT / "plugins").glob(f"*/{row.plugin}/src/*/manifest.json"))
            assert len(manifests) == 1
            from setup_system_sdk import Manifest

            manifest = Manifest.model_validate_json(manifests[0].read_bytes())
            assert row.action in {action.id for action in manifest.actions}


def test_profile_preview_requires_no_apply_and_detaches_saved_values(tmp_path):
    profile = read_profiles(ROOT)["base"]
    calls = []

    class Stub:
        async def activation(self, plugin):
            return SimpleNamespace(manifest=SimpleNamespace(dependencies=[], settings_version=1))

        async def preview(self, plugin, action, options, inputs, *, saved):
            calls.append((plugin, action, options, inputs, saved))
            return (plugin, action)

    store = Settings(tmp_path, tmp_path / "config")
    previews = asyncio.run(preview_profile(Stub(), store, profile))
    assert sorted(previews) == sorted((row.plugin, row.action) for row in profile.actions)
    assert all(not (options or inputs or saved) for _, _, options, inputs, saved in calls)
    assert not store.directory.exists()
