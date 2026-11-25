"""Exercise the actual privileged transaction argument boundary with no host install."""

import contextlib
import json
import runpy
import subprocess
from pathlib import Path

import pytest
from ansible.errors import AnsibleFilterError

ROOT = Path(__file__).resolve().parents[1]


def fixture(tmp_path, monkeypatch, packages):
    module = runpy.run_path(str(ROOT / "ansible/files/apt_apply.py"))
    enter = module["run"]
    scope = enter.__globals__
    (tmp_path / "transaction.json").write_text(
        json.dumps(
            [
                "/untrusted/frontend/apt",
                "--no-remove",
                "--no-install-recommends",
                "install",
                "--",
                *packages,
            ]
        )
    )
    monkeypatch.setitem(scope, "ROOT", ROOT)
    monkeypatch.setitem(scope, "reviewed", lambda stage: None)
    monkeypatch.setitem(scope, "verify_sources", lambda root, vendor: None)
    monkeypatch.setitem(scope, "defer_services", lambda path: contextlib.nullcontext())
    return enter


def test_native_transaction_reconstructs_fixed_sources_and_defers_services(tmp_path, monkeypatch):
    enter = fixture(tmp_path, monkeypatch, ["foot"])
    calls = []

    def record(argv, *, check, env):
        calls.append((argv, env))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", record)
    enter(tmp_path)
    [(args, env)] = calls
    assert args[0] == "/usr/bin/apt-get" and args[-5:] == [
        "--no-remove",
        "--no-install-recommends",
        "install",
        "--",
        "foot",
    ]
    assert any(s.startswith("DPkg::Pre-Install-Pkgs::=") for s in args)
    assert "Dir::State::lists=" + str(tmp_path / "lists") in args
    assert "Dir::Etc::sourceparts=-" in args
    assert env["SYSTEMD_OFFLINE"] == "1" and env["DEBIAN_FRONTEND"] == "noninteractive"
    assert env["APT_CONFIG"] == str(ROOT / "config/base/apt.conf") and all(
        "proxy" not in k.lower() for k in env
    )


@pytest.mark.parametrize(
    "package", ["-o", "../unknown.deb", "/somewhere/happ.deb", "systemd-remover"]
)
def test_unreviewed_user_package_never_reaches_apt(tmp_path, monkeypatch, package):
    enter = fixture(tmp_path, monkeypatch, [package])
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: pytest.fail("An unreviewed package reached APT")
    )
    with pytest.raises(ValueError, match="Unreviewed package"):
        enter(tmp_path)


@pytest.mark.parametrize(
    "preview", ["Remv systemd [1]", "The following packages will be DOWNGRADED:\n example"]
)
def test_native_preview_refuses_removal_or_downgrade(preview):
    native = runpy.run_path(str(ROOT / "ansible/filter_plugins/native.py"))
    with pytest.raises(AnsibleFilterError):
        native["apt_closure"](preview, False)


def test_regional_catalog_change_reaches_preview_and_verified_executor(tmp_path, monkeypatch):
    import shutil
    import tomllib

    import tomlkit

    shutil.copytree(ROOT / "config", tmp_path / "config")
    path = tmp_path / "config/base/packages.toml"
    catalog = tomllib.loads(path.read_text())
    catalog["keyboard"]["stable"] = ["fixture-keyboard"]
    path.write_text(tomlkit.dumps(catalog))
    native = runpy.run_path(str(ROOT / "ansible/filter_plugins/native.py"))
    packages = native["package_names"]('{"keyboard":"fr"}', json.dumps(catalog), "{}")
    assert packages == ["fixture-keyboard"]

    execute = fixture(tmp_path, monkeypatch, packages)
    monkeypatch.setitem(execute.__globals__, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append(argv))
    execute(tmp_path)
    assert len(calls) == 1 and calls[0][-2:] == ["--", "fixture-keyboard"]
