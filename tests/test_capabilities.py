"""Manifest validation must reject malformed capabilities before adapters can act."""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path

import pytest

from setup_system.adapters.debian.hyprland import package_arguments, profile_capabilities
from setup_system.capabilities import CapabilityStatus, load_policy, parse_policy
from setup_system.planning import UserProfile, load_capabilities

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("base_packages", "foot"),
        ("commands", "Hyprland"),
        ("preflight_paths", "config:hypr/"),
        ("architectures", "x86_64"),
        ("apt_architectures", "amd64"),
        ("backport_packages", []),
        ("version_pin", ""),
        ("init_version", None),
    ],
)
def test_hyprland_manifest_fields_are_validated_before_use(
    tmp_path: Path, field: str, value: object
) -> None:
    data = tomllib.loads((ROOT / "manifests/platforms.toml").read_text())
    # TOML cannot encode None, so represent a missing field directly in the fixture text.
    source = (ROOT / "manifests/platforms.toml").read_text()
    if field == "init_version":
        source = source.replace('init_version = "257"\n', "")
    else:
        import json

        original = data["hyprland"][field]
        if isinstance(original, list):
            start = source.index(field + " =", source.index("[hyprland]"))
            end = source.index("\n", source.index("]", start)) + 1
            source = source[:start] + field + " = " + json.dumps(value) + "\n" + source[end:]
        elif field == "backport_packages":
            source = source.replace("[hyprland.backport_packages]", "[hyprland.removed_packages]")
        else:
            source = source.replace(
                field + " = " + json.dumps(original), field + " = " + json.dumps(value)
            )
    folder = tmp_path / "manifests"
    folder.mkdir()
    (folder / "platforms.toml").write_text(source)
    before = {str(p): p.read_bytes() for p in folder.iterdir()}
    with pytest.raises(ValueError):
        load_capabilities(tmp_path)
    assert {str(p): p.read_bytes() for p in folder.iterdir()} == before


@pytest.mark.parametrize(
    ("machine", "canonical", "package_architecture"),
    [
        ("x86_64", "x86_64", "amd64"),
        ("amd64", "x86_64", "amd64"),
        ("aarch64", "arm64", "arm64"),
        ("arm64", "arm64", "arm64"),
    ],
)
def test_typed_architecture_mapping_has_one_manifest_owner(
    machine: str, canonical: str, package_architecture: str
) -> None:
    policy = load_policy(ROOT)
    assert policy.normalize_architecture(machine) == canonical
    assert policy.debian_architecture(machine) == package_architecture


def test_typed_policy_is_immutable_and_dictionary_views_are_detached() -> None:
    policy = load_policy(ROOT)
    assert policy.debian.status is CapabilityStatus.IMPLEMENTED
    assert policy.debian.status.value == "implemented"
    view = policy.document()
    parsed = parse_policy(view)
    assert parsed == policy
    view.clear()
    assert policy.hyprland.version_pin == "0.55.2+ds-1~bpo13+1"
    with pytest.raises(TypeError):
        policy.debian.codenames["13"] = "changed"  # type: ignore[index]
    assert policy.debian_codename("12") == "bookworm"
    assert policy.debian_codename("13") == "trixie"
    assert policy.normalize_architecture("unknown") == "unknown"
    with pytest.raises(ValueError, match="architecture"):
        policy.debian_architecture("unknown")
    with pytest.raises(ValueError, match="codename"):
        policy.debian_codename("99")


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("debian", "codenames", {"12": "bookworm"}),
        ("debian", "apt_architectures", {"x86_64": "arm64", "arm64": "amd64"}),
        ("debian", "locallyVerified", "false"),
        ("debian", "architectures", "x86_64"),
        ("hyprland", "codename", "bookworm"),
        ("hyprland", "distribution", "ubuntu"),
        ("hyprland", "backports", "unstable"),
        ("hyprland", "apt_architectures", ["arm64", "amd64"]),
        ("hyprland", "base_packages", ["foot", "foot"]),
        ("hyprland", "backport_packages", {"bad package": "1.0"}),
    ],
)
def test_inconsistent_capability_fields_are_rejected(
    section: str, field: str, value: object
) -> None:
    raw = tomllib.loads((ROOT / "manifests/platforms.toml").read_text())
    data = copy.deepcopy(raw)
    data[section][field] = value
    with pytest.raises(ValueError):
        parse_policy(data)


@pytest.mark.parametrize(
    "path", ["config:../escape", "data:/absolute", "cache:a//b", "root:etc/file", "state:a\\b"]
)
def test_preflight_destinations_are_validated_at_load(path: str) -> None:
    data = tomllib.loads((ROOT / "manifests/platforms.toml").read_text())
    data["hyprland"]["preflight_paths"] = [path]
    with pytest.raises(ValueError, match="destination"):
        parse_policy(data)


def test_public_package_arguments_keep_the_manifest_order_and_pins() -> None:
    policy = load_policy(ROOT)
    profile = profile_capabilities(ROOT)
    assert profile == load_capabilities(ROOT)["hyprland"]
    assert package_arguments(
        root=ROOT,
        architecture="aarch64",
        os_release={"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"},
    ) == [
        f"hyprland={policy.hyprland.version_pin}",
        *policy.hyprland.base_packages,
        *(f"{name}={version}" for name, version in policy.hyprland.backport_packages.items()),
    ]
    assert not any("JetBrainsMono-" in path for path in policy.hyprland.preflight_paths)


def test_user_profile_enum_preserves_the_public_wire_values() -> None:
    assert [value.value for value in UserProfile] == ["base", "terminalDev", "happ"]
