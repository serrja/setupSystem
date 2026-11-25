"""Simulate Debian inventory and solver replies without touching APT or dpkg."""

import asyncio
import subprocess

import pytest
from pydantic import ValidationError
from setup_plugin_cleanup import Candidates, Plugin, PurgeCandidates, native, prepare

from setup_system_sdk import Outcome, PluginSettings


@pytest.mark.parametrize(
    "flags", [("hold", "no", "no"), ("install", "yes", "no"), ("install", "no", "yes")]
)
def test_held_essential_or_protected(monkeypatch, flags):
    record = "|".join(("reportbug", "all", "installed", *flags, "1.0"))
    monkeypatch.setattr(
        native, "command", lambda *a, **kw: subprocess.CompletedProcess(a, 0, record, "")
    )
    with pytest.raises(ValueError, match="held"):
        native.inventory(["reportbug"], "remove", "amd64")


@pytest.mark.parametrize(
    "text",
    [
        "Remv reportbug [1]\nRemv bash [2]",
        "Purg reportbug [1]",
        "Inst bash",
        "Remv reportbug:i386 [1]",
        "Remv reportbug [3]",
    ],
)
def test_unapproved_closure(text):
    package = native.Package(name="reportbug", architecture="all", version="1", status="installed")
    with pytest.raises(ValueError):
        native.verify_simulation(text, [package], "remove")


def test_purge_and_remove_readback(monkeypatch):
    monkeypatch.setattr(native, "current_target", lambda: ("amd64", True))
    record = "reportbug|all|config-files|deinstall|||1"
    monkeypatch.setattr(
        native, "command", lambda *a, **kw: subprocess.CompletedProcess(a, 0, record, "")
    )
    plan = prepare("remove", Candidates(packages=["reportbug"]))
    assert plan.outcome == Outcome.NO_CHANGES
    assert (
        asyncio.run(
            Plugin().verify(plan, PluginSettings(), PurgeCandidates(packages=["reportbug"]))
        ).outcome
        == Outcome.PARTIAL
    )


@pytest.mark.parametrize("names", [["nano", "nano"], ["bash"], [], ["$(shell whoami)"]])
def test_selection_is_reviewed(names):
    with pytest.raises(ValidationError):
        Candidates(packages=names)


def test_solver_selection_to_protected_apply_then_unprivileged_readback(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import setup_plugin_cleanup as plugin

    package = native.Package(
        name="reportbug", architecture="all", version="2.0", status="installed"
    )
    present = [package]
    inspected = []

    def preview(mode, selected, uid, config):
        assert mode == "remove" and selected == ["reportbug"] and uid == 1009 and config == tmp_path
        inspected.append(mode)
        return list(present)

    monkeypatch.setattr(plugin, "os", SimpleNamespace(getuid=lambda: 1009))
    monkeypatch.setattr(plugin, "user_config_path", lambda: tmp_path)
    monkeypatch.setattr(native, "current_target", lambda: ("amd64", True))
    monkeypatch.setattr(native, "preview", preview)
    monkeypatch.setattr(native, "inventory", lambda *args: list(present))
    monkeypatch.setattr(native, "digest", lambda rows: "b" * 64 if rows == [package] else "a" * 64)
    called = []

    def reviewed(plugin_id, target, payload, method):
        assert (plugin_id, target, method) == ("cleanup", "clean.yml", "sudo")
        change = native.Confirmation.model_validate(payload)
        assert change.mode == "remove" and change.selected == ["reportbug"]
        assert change.expected == "b" * 64
        present.clear()
        called.append(change)

    monkeypatch.setattr(plugin, "run_native_play", reviewed)
    backend, settings, inputs = Plugin(), PluginSettings(), Candidates(packages=["reportbug"])
    assert asyncio.run(backend.choices("candidates", "rtbu", settings)) == ["reportbug"]
    assert "reportbug" in asyncio.run(backend.inspect(settings)).message
    plan = asyncio.run(backend.plan("remove", settings, inputs))
    assert plan.outcome == Outcome.READY and plan.resources[0].effect == "remove"
    assert asyncio.run(backend.verify(plan, settings, inputs)).outcome == Outcome.PARTIAL
    plugin.apply_native("remove", settings, inputs, plan)
    result = asyncio.run(backend.verify(plan, settings, inputs))
    assert result.outcome == Outcome.COMPLETE and result.resources == plan.resources
    assert asyncio.run(backend.plan("remove", settings, inputs)).outcome == Outcome.NO_CHANGES
    with pytest.raises(ValueError, match="closure changed"):
        plugin.apply_native("remove", settings, inputs, plan)
    assert len(called) == 1 and len(inspected) == 4
