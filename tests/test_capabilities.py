"""Manifest validation must reject malformed capabilities before adapters can act."""

from __future__ import annotations

import copy
import json
import runpy
import tomllib
from pathlib import Path

import pytest
from ansible.errors import AnsibleFilterError
from pydantic import ValidationError

from setup_system.infrastructure.native_target import current_target, target
from setup_system.models import PlanSnapshot, WorkspaceOptions, validate_project_settings

ROOT = Path(__file__).resolve().parents[1]
DEBIAN = {"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"}
packages = runpy.run_path(str(ROOT / "ansible/filter_plugins/native.py"))["package_names"]


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("hyprland", "stable", "foot"),
        ("hyprland", "stable", [True]),
        ("hyprland", "stable", [""]),
        ("hyprland", "stable", []),
        ("hyprland", "backports", None),
        ("hyprland", "backports", ["hyprland=1"]),
        ("hyprland", "backports", ["-other"]),
        ("login", "stable", ["../greetd"]),
    ],
)
def test_hyprland_manifest_fields_are_validated_before_use(tmp_path, section, field, value):
    data = tomllib.loads((ROOT / "config/base/packages.toml").read_text())
    if value is None:
        del data[section][field]
    else:
        data[section][field] = value
    before = copy.deepcopy(data)
    with pytest.raises(AnsibleFilterError, match="package"):
        packages(
            json.dumps({"hyprland": True, "graphical_login": True}),
            json.dumps(data),
            "{}",
        )
    assert data == before and not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("machine", "canonical"),
    [("x86_64", "amd64"), ("amd64", "amd64"), ("aarch64", "arm64"), ("arm64", "arm64")],
)
def test_typed_architecture_mapping_has_one_manifest_owner(machine, canonical):
    facts = runpy.run_path(str(ROOT / "ansible/files/facts.py"))
    assert facts["current_target"] is current_target
    assert target(DEBIAN, machine, canonical) == (canonical, True)


def test_typed_policy_is_immutable_and_dictionary_views_are_detached():
    release = dict(DEBIAN)
    facts = {
        "needs_system": True,
        "needs_user": False,
        "architecture": target(release, "arm64", "arm64")[0],
        "distribution": release["VERSION_CODENAME"],
    }
    reply = {
        "status": "ready",
        "actions": [
            {"kind": "region", "field": "timezone", "value": "UTC", "label": "Set regional options"}
        ],
        "observed": facts,
    }
    plan = PlanSnapshot.capture("base", {"timezone": "UTC"}, reply, None)
    release["VERSION_CODENAME"] = "bookworm"
    assert target(release, "arm64", "arm64")[1] is False
    assert plan.payload()["expected"]["observed"]["distribution"] == "trixie"
    output = plan.payload()
    output["expected"]["observed"].clear()
    assert plan.payload()["expected"]["observed"]["architecture"] == "arm64"
    assert target(dict(DEBIAN, VERSION_ID="12"), "arm64", "arm64")[1] is False
    assert target(DEBIAN, "unknown", None) == ("unknown", False)


@pytest.mark.parametrize(
    ("selection", "names"),
    [
        ("desktop", ["foot", "foot"]),
        ("desktop", ["MixedCase"]),
        ("desktop", ["a:amd64"]),
        ("desktop", ["waybar/testing"]),
        ("app", [None]),
        ("app", "vim"),
        ("app", ["git", "git"]),
        ("app", ["../pkg.deb"]),
        ("app", ["git=1"]),
    ],
)
def test_inconsistent_capability_fields_are_rejected(selection, names):
    base = tomllib.loads((ROOT / "config/base/packages.toml").read_text())
    base["hyprland"]["stable"] = names
    with pytest.raises(AnsibleFilterError, match="package"):
        if selection == "desktop":
            packages(json.dumps({"hyprland": True}), json.dumps(base), "{}")
        else:
            packages(
                json.dumps({"apps": ["editor"], "languages": []}),
                "{}",
                json.dumps({"editor": {"packages": names}}),
            )


@pytest.mark.parametrize(
    "path",
    [
        "config:../escape",
        "data:/absolute",
        "cache:a//b",
        "root:etc/file",
        r"state:a\b",
        "/home/u/../other",
    ],
)
def test_resource_destinations_reject_old_prefixes_and_traversal(path):
    with pytest.raises(ValidationError):
        WorkspaceOptions(project=path)
    with pytest.raises(ValidationError):
        validate_project_settings("template-create", {"template": "python", "destination": path})


def test_package_plan_uses_declared_names_and_signed_backports_without_version_pins():
    base = tomllib.loads((ROOT / "config/base/packages.toml").read_text())
    configured = base["hyprland"]
    expected = sorted(
        configured["stable"] + [p + "/trixie-backports" for p in configured["backports"]]
    )
    assert packages(json.dumps({"hyprland": True}), json.dumps(base), "{}") == expected
    assert "hyprland/trixie-backports" in expected and all("=" not in p for p in expected)
    assert packages(json.dumps({"graphical_login": True}), json.dumps(base), "{}") == sorted(
        base["login"]["stable"]
    )
    assert packages("{}", json.dumps(base), "{}") == []


def test_duplicate_packages_in_a_valid_application_are_rejected():
    with pytest.raises(AnsibleFilterError, match="package"):
        packages(
            json.dumps({"apps": ["editor"]}),
            "{}",
            json.dumps({"editor": {"title": "Editor", "kind": "app", "packages": ["git", "git"]}}),
        )
