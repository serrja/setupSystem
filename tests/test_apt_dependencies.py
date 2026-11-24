"""Exercise the real APT resolver with a private, reduced Debian dependency graph.

The cached indexes preserve the relevant trixie/backports versions and dependency
edges. No update, download, package installation or host APT state is involved.
"""

from __future__ import annotations

import os
import re
import runpy
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

STABLE_XKB = "1.7.0-2"
BACKPORT_XKB = "1.13.1-1~bpo13+1"
XKB_PACKAGES = ("libxkbcommon0", "libxkbcommon-x11-0", "libxkbregistry0")
PROFILE_PACKAGES = {"hyprland", "waybar", "qt6-wayland", *XKB_PACKAGES}


def package_record(name: str, version: str, architecture: str, depends: str = "") -> str:
    fields = [
        f"Package: {name}",
        f"Version: {version}",
        f"Architecture: {architecture}",
        f"Filename: pool/{name}_{version}_{architecture}.deb",
        "Size: 1",
        f"Description: Reduced dependency fixture for {name}",
    ]
    if depends:
        fields.append(f"Depends: {depends}")
    return "\n".join(fields) + "\n\n"


def apt_workspace(root: Path, architecture: str, installed: bool) -> Path:
    """Seed lists directly: authentication is tested by the installer, not this solver."""
    for directory in (
        "etc/parts",
        "etc/sources.list.d",
        "etc/preferences.d",
        "state/lists",
        "cache",
        "log",
    ):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "etc/sources.list").write_text(
        "deb https://apt-fixture.invalid/debian trixie main\n"
        "deb https://apt-fixture.invalid/debian trixie-backports main\n"
    )
    (root / "etc/preferences").write_text("")
    status = ""
    if installed:
        for name in XKB_PACKAGES:
            status += (
                f"Package: {name}\nStatus: install ok installed\n"
                f"Architecture: {architecture}\nVersion: {STABLE_XKB}\n"
                + (f"Depends: libxkbcommon0 (= {STABLE_XKB})\n" if name != "libxkbcommon0" else "")
                + "Description: Installed stable fixture\n\n"
            )
    (root / "state/status").write_text(status)
    stable = [
        ("waybar", "0.12.0-1", "libxkbregistry0 (>= 1.0.0)"),
        ("qt6-wayland", "6.8.2-4", "libqt6gui6 (>= 6.8.2+dfsg~)"),
        (
            "libqt6gui6",
            "6.8.2+dfsg-9+deb13u2",
            "libxkbcommon-x11-0 (>= 0.5.0), libxkbcommon0 (>= 0.5.0)",
        ),
        ("libxkbcommon0", STABLE_XKB, ""),
        ("libxkbcommon-x11-0", STABLE_XKB, f"libxkbcommon0 (= {STABLE_XKB})"),
        ("libxkbregistry0", STABLE_XKB, f"libxkbcommon0 (= {STABLE_XKB})"),
    ]
    backports = [
        ("hyprland", "0.55.2+ds-1~bpo13+1", "libxkbcommon0 (>= 1.12.3)"),
        ("libxkbcommon0", BACKPORT_XKB, ""),
        ("libxkbcommon-x11-0", BACKPORT_XKB, f"libxkbcommon0 (= {BACKPORT_XKB})"),
        ("libxkbregistry0", BACKPORT_XKB, f"libxkbcommon0 (= {BACKPORT_XKB})"),
    ]
    for suite, records in (("trixie", stable), ("trixie-backports", backports)):
        prefix = root / f"state/lists/apt-fixture.invalid_debian_dists_{suite}"
        release = (
            "Origin: Debian\nLabel: Debian\n"
            f"Suite: {suite}\nCodename: {suite}\n"
            f"Architectures: {architecture}\nComponents: main\n"
        )
        if suite.endswith("-backports"):
            release += "NotAutomatic: yes\nButAutomaticUpgrades: yes\n"
        prefix.with_name(prefix.name + "_Release").write_text(release)
        prefix.with_name(prefix.name + f"_main_binary-{architecture}_Packages").write_text(
            "".join(
                package_record(name, version, architecture, depends)
                for name, version, depends in records
            )
        )
    configuration = root / "apt.conf"
    configuration.write_text(
        f'Dir "{root}";\n'
        'Dir::Etc "etc";\nDir::Etc::main "absent.conf";\nDir::Etc::parts "parts";\n'
        'Dir::Etc::sourcelist "sources.list";\nDir::Etc::sourceparts "sources.list.d";\n'
        'Dir::Etc::preferences "preferences";\nDir::Etc::preferencesparts "preferences.d";\n'
        'Dir::State "state";\nDir::State::status "status";\nDir::State::lists "lists";\n'
        'Dir::Cache "cache";\nDir::Cache::pkgcache "";\nDir::Cache::srcpkgcache "";\n'
        'Dir::Log "log";\nDebug::NoLocking "true";\n'
        f'APT::Architecture "{architecture}";\nAPT::Architectures {{ "{architecture}"; }};\n'
        'Acquire::Languages "none";\n'
    )
    return configuration


def run_apt(
    configuration: Path, executable: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    command = shutil.which(executable)
    assert command is not None, f"{executable} is required for the isolated APT solver regression"
    return subprocess.run(
        [command, *arguments],
        env={"PATH": os.defpath, "APT_CONFIG": str(configuration), "LC_ALL": "C"},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def profile_arguments() -> list[str]:
    native = runpy.run_path(str(ROOT / "ansible/filter_plugins/native.py"))
    catalog = tomllib.loads((ROOT / "config/base/packages.toml").read_text())
    selected = native["package_names"]({"hyprland": True}, catalog, {})
    return [p for p in selected if p.partition("/")[0] in PROFILE_PACKAGES]


def simulate(configuration: Path, packages: list[str]) -> subprocess.CompletedProcess[str]:
    return run_apt(
        configuration,
        "apt-get",
        "--simulate",
        "--no-remove",
        "--no-install-recommends",
        "install",
        *packages,
    )


@pytest.mark.integration
@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
@pytest.mark.parametrize("installed", [False, True], ids=["clean", "stable-installed"])
def test_profile_resolves_coherent_xkb_backports_without_removal(
    tmp_path: Path, architecture: str, installed: bool
) -> None:
    configuration = apt_workspace(tmp_path, architecture, installed)
    before = (tmp_path / "state/status").read_bytes()
    policy = run_apt(configuration, "apt-cache", "policy", *XKB_PACKAGES)
    assert policy.returncode == 0, policy.stderr
    assert policy.stdout.count(f"Candidate: {STABLE_XKB}") == 3, policy.stdout
    assert f"{BACKPORT_XKB} 100" in policy.stdout, policy.stdout
    assert f"{STABLE_XKB} 500" in policy.stdout, policy.stdout
    result = simulate(configuration, profile_arguments())
    assert result.returncode == 0, result.stdout + result.stderr
    for name in XKB_PACKAGES:
        planned = next(
            line for line in result.stdout.splitlines() if line.startswith(f"Inst {name} ")
        )
        assert f"({BACKPORT_XKB} " in planned, result.stdout
    assert "Remv " not in result.stdout
    assert "downgraded" not in result.stdout.lower()
    repeated = simulate(configuration, profile_arguments())
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert repeated.stdout == result.stdout
    assert (tmp_path / "state/status").read_bytes() == before

    # Model the completed package stage, then resolve a second installer run.
    # This changes only the private dpkg fixture; APT never applies this plan.
    selected = {
        (match.group(1), match.group(2))
        for line in result.stdout.splitlines()
        if (match := re.match(r"Inst (\S+)(?: \[[^]]+\])? \((\S+) ", line))
    }
    assert {name for name, _ in selected} == PROFILE_PACKAGES | {"libqt6gui6"}
    records = []
    for index in (tmp_path / "state/lists").glob("*_Packages"):
        for record in index.read_text().strip().split("\n\n"):
            fields = dict(line.split(": ", 1) for line in record.splitlines())
            if (fields["Package"], fields["Version"]) in selected:
                records.append(record + "\nStatus: install ok installed\n\n")
    (tmp_path / "state/status").write_text("".join(records))
    completed = simulate(configuration, profile_arguments())
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not any(line.startswith(("Inst ", "Remv ")) for line in completed.stdout.splitlines())


@pytest.mark.integration
@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
@pytest.mark.parametrize(
    "partial_pins", [0, 1, 2], ids=["old-profile", "common-only", "missing-registry"]
)
def test_resolver_rejects_incomplete_xkb_backports_cohort(
    tmp_path: Path, architecture: str, partial_pins: int
) -> None:
    configuration = apt_workspace(tmp_path, architecture, installed=False)
    packages = [
        argument
        for argument in profile_arguments()
        if argument.partition("/")[0] not in XKB_PACKAGES
    ]
    packages.extend(f"{name}/trixie-backports" for name in XKB_PACKAGES[:partial_pins])
    packages.extend(f"{name}/trixie" for name in XKB_PACKAGES[partial_pins:])
    result = simulate(configuration, packages)
    assert result.returncode == 100, result.stdout + result.stderr
    assert "libxkbcommon0" in result.stdout + result.stderr
    assert "unmet dependencies" in result.stdout
    if partial_pins:
        assert STABLE_XKB in result.stdout + result.stderr
