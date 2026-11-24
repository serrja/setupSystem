"""Resolve supported vendor service states from the native manager without a release pin."""

import subprocess

import pytest

from setup_system.infrastructure import happ_facts


@pytest.mark.parametrize(
    ("loaded", "active", "status"),
    [("not-found", "inactive", 4), ("loaded", "inactive", 0), ("loaded", "active", 0)],
)
def test_native_systemd_capability_replaces_a_version_pin(
    tmp_path, monkeypatch, loaded, active, status
):
    unit = tmp_path / "happd.service"
    monkeypatch.setattr(happ_facts, "UNIT_PATHS", (unit,))
    monkeypatch.setattr(happ_facts, "protected", lambda path, **kw: None)

    def systemctl(argv, **kwargs):
        assert argv[0] == "/usr/bin/systemctl" and "show" in argv and "happd.service" in argv
        assert not {"start", "restart", "unmask", "enable"} & set(argv)
        source = str(unit) if loaded == "loaded" else ""
        return subprocess.CompletedProcess(
            argv, status, f"LoadState={loaded}\nActiveState={active}\nFragmentPath={source}\n"
        )

    monkeypatch.setattr(subprocess, "run", systemctl)
    assert happ_facts.unit_info()["ActiveState"] == active


@pytest.mark.parametrize(
    ("status", "properties"),
    [
        (1, ""),
        (0, "LoadState=masked\nActiveState=inactive\nFragmentPath=/dev/null"),
        (0, "LoadState=error\nActiveState=failed\nFragmentPath="),
        (0, "LoadState=loaded\nActiveState=active\nFragmentPath=/home/user/happd.service"),
    ],
)
def test_failed_or_unreviewed_manager_refuses_activation(tmp_path, monkeypatch, status, properties):
    monkeypatch.setattr(happ_facts, "UNIT_PATHS", (tmp_path / "unused",))
    monkeypatch.setattr(happ_facts, "protected", lambda path, **kwargs: None)
    monkeypatch.setattr(
        subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(args, status, properties)
    )
    with pytest.raises(ValueError, match=r"system manager|Unexpected Happ"):
        happ_facts.unit_info()
