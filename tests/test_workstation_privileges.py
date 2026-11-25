"""Keep user publications and the protected Make adapter at the right privilege."""

import asyncio
import os

import pytest

from setup_system.infrastructure.managed_files import apply_files
from setup_system_sdk import Mutation, Outcome, Plan
from setup_system_sdk.native import NativeBackend
from setup_system_sdk.native_play import run_native_play


def test_root_configuration_rejected_before_resource_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(ValueError, match="root"):
        apply_files({})
    backend = NativeBackend()
    request = Mutation(action_id="configure", settings={}, inputs={}, plan=Plan(summary="fixture"))
    assert asyncio.run(backend.apply_request("configure", request)).outcome == Outcome.BLOCKED
    with pytest.raises(ValueError, match="normal-user"):
        run_native_play("fixture", "configure.yml", {}, "sudo")
    assert not list(tmp_path.iterdir())
