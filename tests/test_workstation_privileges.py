"""Never publish user files or inspect a workstation profile as root."""

import io
import json
import os
import runpy
import sys
from pathlib import Path

import pytest

from setup_system import file_tasks
from setup_system.infrastructure.managed_files import apply_files

ROOT = Path(__file__).resolve().parents[1]


def test_root_user_configuration_is_rejected_before_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(ValueError, match="root"):
        apply_files({})
    facts = runpy.run_path(str(ROOT / "ansible/files/facts.py"))
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"section": "base", "intent": {}, "inspect": False}))
    )
    with pytest.raises(ValueError, match="ordinary account"):
        facts["main"]()
    monkeypatch.setattr(os, "getuid", lambda: 0)
    with pytest.raises(SystemExit, match="normal user"):
        file_tasks.main()
    assert not list(tmp_path.iterdir())
