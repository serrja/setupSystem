"""Verify native package reconstruction against a protected package snapshot and fixture calls."""

import subprocess
import tempfile
from contextlib import nullcontext

import pytest

from setup_system_sdk import Plan, Resource, apt


def fixture(tmp_path, monkeypatch):
    folder = tmp_path / "plugins/example/setup_plugin_example"
    folder.mkdir(parents=True)
    (folder / "apt.json").write_text('{"names":["tree"]}')
    plan = Plan(
        summary="Confirmed",
        resources=[
            Resource(kind="package", identity="tree", ownership="created", after="1#" + "f" * 64)
        ],
    )
    approval = apt.Approval(plugin_id="example", action_id="install", plan=plan)
    temp = tempfile.TemporaryDirectory
    monkeypatch.setattr(apt.tempfile, "TemporaryDirectory", lambda **kw: temp(dir=tmp_path))
    monkeypatch.setattr(apt, "INSTALLED", tmp_path)
    monkeypatch.setattr(apt.os, "geteuid", lambda: 0)
    monkeypatch.setattr(apt, "current_target", lambda: ("amd64", True))

    def require(source, relative):
        assert source == tmp_path / relative == folder / "apt.json"

    monkeypatch.setattr(apt, "require_installed_resource", require)
    monkeypatch.setattr(apt, "defer_services", lambda path: nullcontext())
    return approval


def test_native_transaction_reconstructs_fixed_sources_and_defers_services(tmp_path, monkeypatch):
    approval = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(apt, "resolve", lambda *a: approval.plan)
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)))
    apt.privileged(approval)
    [(args, kwargs)] = calls
    assert args[0] == "/usr/bin/apt-get" and args[-5:] == [
        "--no-remove",
        "--no-install-recommends",
        "install",
        "--",
        "tree",
    ]
    assert any(s.startswith("DPkg::Pre-Install-Pkgs::=") for s in args)
    assert any(s.startswith("Dir::State::lists=") for s in args)
    assert "Dir::Etc::sourceparts=-" in args
    assert kwargs["check"] and all(
        kwargs[k] == subprocess.DEVNULL for k in ("stdin", "stdout", "stderr")
    )
    env = kwargs["env"]
    assert env["SYSTEMD_OFFLINE"] == "1" and env["DEBIAN_FRONTEND"] == "noninteractive"
    assert env["APT_CONFIG"] == str(tmp_path / "config/base/apt.conf")
    assert not any("proxy" in name.lower() for name in env)


def test_native_boundary_refuses_changed_package_transaction(tmp_path, monkeypatch):
    approval = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(apt, "resolve", lambda *a: Plan(summary="Changed"))
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: pytest.fail("Unexpected package change")
    )
    with pytest.raises(ValueError, match="changed"):
        apt.privileged(approval)


@pytest.mark.parametrize(
    "preview", ["Remv systemd [1]", "The following packages will be DOWNGRADED:\n example"]
)
def test_native_preview_refuses_removal_or_downgrade(preview):
    with pytest.raises(ValueError):
        apt.closure(preview)
