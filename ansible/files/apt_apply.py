"""Run a native APT transaction with a bounded before-dpkg approval hook."""

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from pydantic import TypeAdapter

from setup_system.domain.applications import BasePackages, applications
from setup_system.infrastructure import happ_facts
from setup_system.infrastructure.downloads import DownloadPolicy, read_https
from setup_system.infrastructure.native_target import current_target
from setup_system.infrastructure.service_policy import defer_services
from setup_system.infrastructure.system_paths import protected
from setup_system.infrastructure.upstream_asset import github_deb
from setup_system.layout import INSTALLED, require_installed_resource

ROOT = INSTALLED
APT_HELPER = Path("ansible/files/apt_apply.py")


def reviewed(stage: Path) -> None:
    if stage.parent != Path("/run/setup-system") or not re.fullmatch(
        r"setup-system-apt-[A-Za-z0-9_-]+", stage.name
    ):
        raise ValueError("Expected a private installed system transaction")
    protected(stage)
    for name in ("transaction.json", "approved.json"):
        path = stage / name
        protected(path, directory=False)
        if path.stat().st_size > 2_000_000 or stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise ValueError("Invalid system package transaction receipt")


def verify(stage: Path) -> None:
    approved = TypeAdapter(list[str]).validate_json((stage / "approved.json").read_bytes())
    expected = {}
    architecture = subprocess.check_output(
        ["/usr/bin/dpkg", "--print-architecture"], text=True
    ).strip()
    for line in approved:
        match = re.match(r"(Inst|Conf) (\S+)(?: \[([^]]+)\])? \((\S+)", line)
        if not match:
            raise ValueError("Unsupported native APT simulation record")
        op, name, old, version = match.groups()
        package, _, arch = name.partition(":")
        if (arch and arch != architecture) or (op, package) in expected:
            raise ValueError("Duplicate or foreign native APT transaction record")
        expected[op, package] = (old or "-", version.rstrip(")"))
    stream = iter(sys.stdin)
    if next(stream, "").strip() != "VERSION 3":
        raise ValueError("APT must provide its native version 3 pre-install protocol")
    for line in stream:
        if not line.strip():
            break
    found = set()
    for row in stream:
        fields = row.rstrip().split(maxsplit=8)
        if len(fields) != 9:
            raise ValueError("Invalid native APT package action")
        name, previous, _, _, direction, version, arch, _, action = fields
        op = "Conf" if action == "**CONFIGURE**" else "Inst"
        selected = expected.get((op, name))
        if (
            direction not in {"<", "="}
            or selected is None
            or selected[1] != version
            or (op == "Inst" and (not action.endswith(".deb") or previous != selected[0]))
        ):
            raise ValueError("APT changed the confirmed package closure")
        if arch not in {architecture, "all"}:
            raise ValueError("Unapproved package architecture")
        if (op, name) in found:
            raise ValueError("Duplicate package in the actual native transaction")
        found.add((op, name))
    if set(expected) != found:
        raise ValueError("The actual native APT transaction differs from the confirmed plan")


def keyring(path: Path) -> None:
    if path.is_symlink() or not path.is_file() or not path.stat().st_size:
        raise ValueError("Required Debian archive keyring is missing or not regular")
    protected(path, directory=False)


def verify_sources(root: Path, vendor: bool) -> None:
    sources = [root / "config/base/debian.sources"]
    if vendor:
        sources.append(root / "config/base/optional_sources/chatgpt.sources")
    for source in sources:
        anchors = set(re.findall(r"(?m)^Signed-By: (/[^\s]+)$", source.read_text()))
        if not anchors:
            raise ValueError("The selected APT source lacks its signed-by keyring")
        for anchor in anchors:
            keyring(Path(anchor))


def validate_deb(path: Path, key: str, verified: dict[str, str], arch: str) -> str:
    with path.open("rb") as stream:
        if "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest() != verified["checksum"]:
            raise ValueError("Upstream package changed after confirmation")
    header = subprocess.check_output(
        [
            "/usr/bin/dpkg-deb",
            "--show",
            "--showformat=${Package}|${Architecture}|${Version}",
            str(path),
        ],
        text=True,
    )
    name, deb_arch, version = header.split("|")
    if name != key or arch != deb_arch or version.split("-")[0] != verified["version"]:
        raise ValueError("Unexpected Debian package identity")
    return version


def run(stage: Path) -> None:
    reviewed(stage)
    args = TypeAdapter(list[str]).validate_json((stage / "transaction.json").read_bytes())
    package_config = BasePackages.model_validate(
        tomllib.loads((ROOT / "config/base/packages.toml").read_text())
    )
    app_config = applications(
        tomllib.loads((ROOT / "config/workspace/applications.toml").read_text())
    )
    allow = set(package_config.hyprland.stable + package_config.login.stable)
    allow.update(p + "/trixie-backports" for p in package_config.hyprland.backports)
    allow.update(p for app in app_config.values() for p in app.packages)
    allow.update(["console-setup", "locales", "plymouth", "plymouth-themes", "xkb-data"])
    index = args.index("--")
    if args[index - 3 : index] != ["--no-remove", "--no-install-recommends", "install"]:
        raise ValueError("Expected a native nonremoving install")
    packages = args[index + 1 :]
    for name in packages:
        if name in allow:
            continue
        path = Path(name)
        if path.parent != stage or not path.name.endswith(".deb"):
            raise ValueError("Unreviewed package or source option")
        protected(path, directory=False)
        records = json.loads((stage / "upstream.json").read_bytes())
        candidates = [
            key
            for key, entry in records.items()
            if key in {"happ", "obsidian"} and entry["name"] == path.name
        ]
        if len(candidates) != 1:
            raise ValueError("Unreviewed upstream Debian source")
        key = candidates[0]
        source = app_config[key]
        if not source.github:
            raise ValueError("No approved upstream source for the selected Debian archive")
        url = "https://api.github.com/repos/" + source.github + "/releases/latest"
        raw = read_https(
            url,
            DownloadPolicy(frozenset({"api.github.com"}), 1024 * 1024, 30, follow_redirects=False),
        )
        arch = current_target()[0]
        verified = github_deb(json.loads(raw), source.model_dump(mode="json"), arch)
        accepted = dict(records[key])
        before = accepted.pop("before", None)
        if accepted != verified:
            raise ValueError("Latest stable release differs from the confirmed selection")
        version = validate_deb(path, key, verified, arch)
        if key == "happ":
            happ_facts.verify_before(before, version)
    verify_sources(ROOT, "chatgpt" in packages)
    # Rebuild the native argument vector from protected sources. Treat the request as data.
    args = [
        "/usr/bin/apt-get",
        "-o",
        "Dir::Etc::sourcelist=" + str(ROOT / "config/base/debian.sources"),
        "-o",
        "Dir::Etc::sourceparts="
        + (str(ROOT / "config/base/optional_sources") if "chatgpt" in packages else "-"),
        "-o",
        "Dir::Etc::preferences=" + str(ROOT / "config/base/sources.pref"),
        "-o",
        "Dir::State::lists=" + str(stage / "lists"),
        "-o",
        "Dir::Cache::archives=" + str(stage / "archives"),
        "-o",
        "Dpkg::Options::=--force-confold",
        "--assume-yes",
        "--no-remove",
        "--no-install-recommends",
        "install",
        "--",
        *packages,
    ]
    hook = f"/usr/bin/python3 -I -B {INSTALLED / APT_HELPER} verify {stage}"
    config = [
        "-o",
        "DPkg::Pre-Install-Pkgs::=" + hook,
        "-o",
        "DPkg::Tools::Options::/usr/bin/python3::Version=3",
    ]
    # APT indexes hook protocol options by the executable name (first command word).
    with defer_services(Path("/usr/sbin/policy-rc.d")):
        subprocess.run(
            [args[0], *config, *args[1:]],
            check=True,
            env={
                "LC_ALL": "C",
                "HOME": "/root",
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "DEBIAN_FRONTEND": "noninteractive",
                "SYSTEMD_OFFLINE": "1",
                "APT_CONFIG": str(ROOT / "config/base/apt.conf"),
            },
        )


class Vendor(StrEnum):
    CHATGPT = "chatgpt"


cli = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


@cli.command()
def sources(vendor: Annotated[Vendor | None, typer.Argument()] = None) -> None:
    """Check the signed-by keyrings selected by this stage."""
    verify_sources(Path(__file__).absolute().parents[2], vendor == Vendor.CHATGPT)


def system_boundary() -> None:
    if os.geteuid() != 0 or not current_target()[1]:
        raise ValueError("Only the installed system executor can run this boundary")
    require_installed_resource(Path(__file__), str(APT_HELPER))


@cli.command("verify")
def verify_hook(stage: Path) -> None:
    """Compare the actual APT pre-install protocol with the confirmed transaction."""
    system_boundary()
    reviewed(stage)
    verify(stage)


@cli.command()
def apply(stage: Path) -> None:
    """Execute the reviewed APT transaction."""
    system_boundary()
    run(stage)


if __name__ == "__main__":
    cli()
