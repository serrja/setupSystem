"""Distinguish absent, healthy, held and broken vendor package state before upgrades."""

import subprocess

import pytest

from setup_system_sdk import apt


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
        assert argv[-1] == "happ" and kwargs["timeout"] == 5
        return subprocess.CompletedProcess(argv, code, status, error)

    monkeypatch.setattr(apt, "run_command", query)
    assert apt.installed("happ") == expected


@pytest.mark.parametrize(
    ("code", "status"), [(1, ""), (2, ""), (0, "install ok unpacked|1.2"), (0, "unknown")]
)
def test_query_failure_or_partial_state_cannot_mean_absent(monkeypatch, code, status):
    monkeypatch.setattr(
        apt,
        "run_command",
        lambda args, **kw: subprocess.CompletedProcess(args, code, status, "database failure"),
    )
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        apt.installed("happ")


@pytest.mark.parametrize(
    ("loaded", "active", "status"),
    [("not-found", "inactive", 4), ("loaded", "inactive", 0), ("loaded", "active", 0)],
)
def test_native_systemd_capability_replaces_a_version_pin(
    tmp_path, monkeypatch, loaded, active, status
):
    unit = tmp_path / "happd.service"
    monkeypatch.setattr(apt, "UNIT_ROOTS", (tmp_path,))
    monkeypatch.setattr(apt, "protected", lambda path, **kw: None)

    def systemctl(argv, **kwargs):
        assert argv[0] == "/usr/bin/systemctl" and "show" in argv and "happd.service" in argv
        assert not {"start", "restart", "unmask", "enable"} & set(argv)
        source = str(unit) if loaded == "loaded" else ""
        return subprocess.CompletedProcess(
            argv, status, f"LoadState={loaded}\nActiveState={active}\nFragmentPath={source}\n"
        )

    monkeypatch.setattr(apt, "run_command", systemctl)
    assert apt.unit_info("happd.service")["ActiveState"] == active


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
    monkeypatch.setattr(apt, "UNIT_ROOTS", (tmp_path,))
    monkeypatch.setattr(apt, "protected", lambda path, **kwargs: None)
    monkeypatch.setattr(
        apt,
        "run_command",
        lambda args, **kw: subprocess.CompletedProcess(args, status, properties),
    )
    with pytest.raises(ValueError, match=r"system manager|Unexpected system"):
        apt.unit_info("happd.service")


@pytest.mark.parametrize(
    "active, process", [("active", False), ("inactive", True), ("inactive", False)]
)
def test_upgrades_require_idle_session(monkeypatch, active, process):
    policy = apt.Packages(
        names=("happ",),
        requires_idle=apt.IdleRequirement(service="happd.service", processes=("Happ", "happ")),
    )
    monkeypatch.setattr(apt, "unit_info", lambda _: {"ActiveState": active})
    monkeypatch.setattr(
        apt,
        "run_command",
        lambda args, **kw: subprocess.CompletedProcess(args, 0 if process else 1),
    )
    if active == "active" or process:
        with pytest.raises(ValueError):
            apt.require_idle(policy)
    else:
        apt.require_idle(policy)


def test_administrative_mask_protected(tmp_path, monkeypatch):
    (tmp_path / "happd.service").symlink_to("/dev/null")
    monkeypatch.setattr(apt, "UNIT_ROOTS", (tmp_path,))
    with pytest.raises(ValueError, match="mask"):
        apt.unit_info("happd.service")


def test_failed_system_unit_readback_preserves_package_state(monkeypatch):
    import asyncio

    import setup_plugin_happ

    from setup_system_sdk import Inputs, Outcome, PluginSettings

    plugin = setup_plugin_happ.Plugin()
    facts = {"LoadState": "loaded", "ActiveState": "inactive"}
    monkeypatch.setattr(apt, "unit_info", lambda _: dict(facts))
    plan = plugin.prepare("start-system-service", PluginSettings(), Inputs())
    assert plan.resources[0].identity == "system/happd.service"
    assert asyncio.run(plugin.verify(plan, PluginSettings(), Inputs())).outcome == Outcome.PARTIAL
    facts["ActiveState"] = "active"
    assert asyncio.run(plugin.verify(plan, PluginSettings(), Inputs())).outcome == Outcome.COMPLETE
