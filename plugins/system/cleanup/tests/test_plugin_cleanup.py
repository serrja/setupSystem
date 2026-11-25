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
