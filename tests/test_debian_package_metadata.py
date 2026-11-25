"""Distinguish absent, healthy, held and broken vendor package state before upgrades."""

import subprocess

import pytest

from setup_system.infrastructure import happ_facts


@pytest.mark.parametrize(
    ("status", "code", "error", "expected"),
    [
        ("install ok installed|1.2-34", 0, "", "1.2-34"),
        ("hold ok installed|1.2-34", 0, "", "1.2-34"),
        ("deinstall ok config-files|1.2-34", 0, "", None),
        ("", 1, "dpkg-query: no packages found matching happ", None),
    ],
)
def test_inspect_installed_state(monkeypatch, status, code, error, expected):
    def query(argv, **kwargs):
        assert argv[-1] == "happ" and kwargs["timeout"] == 15
        return subprocess.CompletedProcess(argv, code, status, error)

    monkeypatch.setattr(happ_facts, "run_command", query)
    assert happ_facts.installed_version() == expected


@pytest.mark.parametrize(
    ("code", "status"), [(1, ""), (2, ""), (0, "install ok unpacked|1.2"), (0, "unknown")]
)
def test_query_failure_or_partial_state_cannot_mean_absent(monkeypatch, code, status):
    monkeypatch.setattr(
        happ_facts,
        "run_command",
        lambda args, **kw: subprocess.CompletedProcess(args, code, status, "database failure"),
    )
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        happ_facts.installed_version()


@pytest.mark.parametrize("process", ["Happ", "happ"])
def test_existing_connection_blocks_upgrade(monkeypatch, process):
    def running(args, **kw):
        return subprocess.CompletedProcess(args, 0 if args[-1] == process else 1)

    monkeypatch.setattr(happ_facts, "run_command", running)
    with pytest.raises(ValueError, match="disconnect"):
        happ_facts.require_idle("1.0-1", "2.0", {"ActiveState": "inactive"})
    with pytest.raises(ValueError, match="active service"):
        happ_facts.require_idle("1.0-1", "2.0", {"ActiveState": "active"})
    happ_facts.require_idle(None, "2.0", {"ActiveState": "inactive"})
    happ_facts.require_idle("2.0-40", "2.0-40", {"ActiveState": "active"})
