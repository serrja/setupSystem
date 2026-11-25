"""Desktop consumers use the canonical program owners and signed suites."""

import json
from pathlib import Path

import pytest

from setup_system_sdk import Manifest
from setup_system_sdk.apt import Packages


@pytest.mark.parametrize("name", ["waybar", "foot", "hyprlock"])
def test_companion_program_has_exactly_one_owner(name):
    desktop = Path(__file__).resolve().parents[2]
    hyprland = desktop / "hyprland/src/setup_plugin_hyprland"
    dependency = desktop / name / "src" / ("setup_plugin_" + name)
    manager = Manifest.model_validate_json((hyprland / "manifest.json").read_bytes())
    assert name in manager.dependencies
    assert name not in Packages.model_validate_json((hyprland / "apt.json").read_bytes()).names
    assert name in Packages.model_validate_json((dependency / "apt.json").read_bytes()).names
    assert "hyprland" not in json.loads((dependency / "manifest.json").read_bytes())["dependencies"]


def test_program_removal_preserves_a_registered_desktop_consumer(tmp_path, monkeypatch):
    import asyncio
    from uuid import uuid4

    from setup_system.resource_ledger import ResourceLedger
    from setup_system_sdk import Outcome, Plan, Resource, Result

    monkeypatch.setenv("HOME", str(tmp_path))

    async def exercise():
        ledger = ResourceLedger(tmp_path / "ledger.db")
        installed = Resource(
            kind="package",
            identity="waybar",
            ownership="created",
            after="1.0",
            cleanup=True,
            consumers=("hyprland",),
        )
        operation = uuid4()
        await ledger.prepare(
            operation, "waybar", "install", "a" * 64, Plan(summary="Install", resources=[installed])
        )
        await ledger.applied(operation)
        await ledger.finish(
            operation, Result(outcome=Outcome.COMPLETE, message="Verified", resources=[installed])
        )
        remove = Resource(
            kind="package",
            identity="waybar",
            ownership="created",
            before="1.0",
            cleanup=True,
            effect="remove",
        )
        with pytest.raises(ValueError, match="needs"):
            await ledger.prepare(
                uuid4(), "waybar", "uninstall", "a" * 64, Plan(summary="Remove", resources=[remove])
            )
        assert (await ledger.holdings("waybar"))[0].after == "1.0"

    asyncio.run(exercise())
