"""Resolve and verify immutable Debian package transactions for a trusted plugin."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from setup_system_sdk.downloads import GITHUB_RELEASE_HOSTS, DownloadPolicy, read_https
from setup_system_sdk.enums.lifecycle import LifecycleAction, PackageAction, ServiceAction
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.resources import ResourceEffect, ResourceOwnership
from setup_system_sdk.files.system_paths import protected
from setup_system_sdk.plugin import owned_resources
from setup_system_sdk.processes import CancellationPolicy, run_command
from setup_system_sdk.releases import GithubRelease
from setup_system_sdk.schemas.common import Identifier, Inputs
from setup_system_sdk.schemas.operation import Plan, Resource, Result

from setup_plugin_debian.adapters.layout import INSTALLED, require_installed_resource, resource_root
from setup_plugin_debian.adapters.native import run_native_play
from setup_plugin_debian.adapters.platform import current_target
from setup_plugin_debian.adapters.service_policy import defer_services

PackageName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9+.-]+$", max_length=128)]
SOURCE = "config/base/debian.sources"


class DebianRelease(Inputs):
    github: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*/[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    asset: dict[str, str]


class IdleRequirement(Inputs):
    service: str = Field(pattern=r"^[a-zA-Z0-9_-]+\.service$")
    processes: tuple[Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]+$")], ...] = Field(max_length=8)


class Packages(Inputs):
    names: tuple[PackageName, ...] = Field(max_length=64)
    retired_names: tuple[PackageName, ...] = Field(default=(), max_length=64)
    backports: tuple[PackageName, ...] = ()
    vendor_source: bool = False
    release: DebianRelease | None = None
    requires_idle: IdleRequirement | None = None
    removal_guard: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,47}\.py$")
    service: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]+\.service$")

    @model_validator(mode="after")
    def distinct_sources(self) -> Self:
        if not self.names and (not self.retired_names or self.release or self.service):
            raise ValueError("A removal-only package catalog requires retired package names")
        if (
            len(self.names) != len(set(self.names))
            or len(self.retired_names) != len(set(self.retired_names))
            or set(self.names).intersection(self.retired_names)
            or len(self.backports) != len(set(self.backports))
            or set(self.backports) - set(self.names)
            or (self.release and self.backports)
        ):
            raise ValueError("Package names and optional backports must be unique and reviewed")
        return self

    def accepts_removal(self, name: str) -> bool:
        return name in self.names or name in self.retired_names

    def check_retired_selection(self, rows: list[Resource]) -> None:
        if (
            len(rows) != 1
            or rows[0].identity not in self.retired_names
            or rows[0].effect != "remove"
        ):
            raise ValueError("Select exactly one reviewed retired package")

    def candidates(self) -> list[str]:
        return [
            name + "/trixie-backports" if name in self.backports else name for name in self.names
        ]


SERVICE_TRANSITIONS = {
    ServiceAction.START: ("start", "active"),
    ServiceAction.STOP: ("stop", "inactive"),
}


REMOVALS = frozenset({LifecycleAction.UNINSTALL, PackageAction.REMOVE_RETIRED})


class RetiredPackage(Inputs):
    name: PackageName = Field(
        title="Formerly managed package",
        description="Choose one package removed from this plugin's install list.",
        json_schema_extra={"ui": {"choice_provider": "retired-packages"}},
    )


class Approval(Inputs):
    plugin_id: Identifier
    action_id: (
        Literal[LifecycleAction.INSTALL, LifecycleAction.UPDATE, LifecycleAction.UNINSTALL]
        | PackageAction
        | ServiceAction
    )
    plan: Plan


class Authorization(Inputs):
    approval: Approval


def apt_environment(root: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LC_ALL": "C",
        "HOME": "/",
        "APT_CONFIG": str(root / "config/base/apt.conf"),
        "DEBIAN_FRONTEND": "noninteractive",
        "SYSTEMD_OFFLINE": "1",
    }


def query(argv: list[str], root: Path, timeout: int = 30, *, stage: Path | None = None) -> str:
    result = run_command(
        argv,
        env=apt_environment(root) | ({"TMPDIR": str(stage)} if stage else {}),
        cwd="/",
        stdin=subprocess.DEVNULL,
        discard_stderr=True,
        check=False,
        max_output_bytes=2_000_000,
        timeout=timeout,
    )
    if result.returncode:
        raise ValueError("APT could not resolve the approved signed package source")
    return result.stdout


def installed(package: str) -> str | None:
    result = run_command(
        ["/usr/bin/dpkg-query", "-W", "-f=${Status}|${Version}", "--", package],
        stdin=subprocess.DEVNULL,
        check=False,
        max_output_bytes=4096,
        timeout=5,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    )
    if (
        result.returncode == 1
        and not result.stdout
        and result.stderr.strip().lower() == "dpkg-query: no packages found matching " + package
    ):
        return None
    if result.returncode or "|" not in result.stdout:
        raise ValueError("Cannot read installed Debian package state")
    status, version = result.stdout.strip().split("|", 1)
    if not re.fullmatch(r"[0-9A-Za-z.+:~-]+", version):
        raise ValueError("Invalid installed package version")
    if status in {"hold ok installed", "install ok installed"}:
        return version
    if status.endswith((" ok not-installed", " ok config-files")):
        return None
    raise ValueError("Repair the partial dpkg state before installing packages")


def options(root: Path, stage: Path, folder: Path, packages: Packages) -> list[str]:
    return [
        "-o",
        f"Dir::Etc::sourcelist={root / SOURCE}",
        "-o",
        "Dir::Etc::sourceparts=" + (str(folder / "sources") if packages.vendor_source else "-"),
        "-o",
        "Dir::Etc::preferences="
        + (str(folder / "preferences") if packages.vendor_source else "/dev/null"),
        "-o",
        f"Dir::State::lists={stage / 'lists'}",
        "-o",
        f"Dir::Cache::archives={stage / 'archives'}",
        "-o",
        "Dpkg::Options::=--force-confold",
    ]


def closure(output: str) -> dict[str, tuple[str | None, str]]:
    if "The following packages will be DOWNGRADED:" in output:
        raise ValueError("A downgrade requires a separate reviewed transaction")
    installs = {}
    configured = {}
    for row in output.splitlines():
        if row.startswith(("Remv ", "Purg ", "Del ")):
            raise ValueError("Install would remove packages")
        if row.startswith(("Inst ", "Conf ")):
            match = re.fullmatch(
                r"(Inst|Conf) ([a-z0-9][a-z0-9+.-]+)(?: \[([^]\s]+)\])? "
                r"\(([^ )]+)(?: [^)]*)?\)(?: \[\])?",
                row,
            )
            if not match:
                raise ValueError("Unsupported or foreign APT action")
            verb, package, old, version = match.groups()
            if verb == "Inst":
                if package in installs:
                    raise ValueError("Duplicate package in simulation")
                installs[package] = (old, version)
            else:
                if package in configured:
                    raise ValueError("Duplicate configure step")
                configured[package] = version
    if configured != {package: new for package, (_, new) in installs.items()}:
        raise ValueError("Unmatched package actions require dpkg recovery")
    return installs


def archive(stage: Path, packages: Packages) -> tuple[Path, str] | None:
    release = packages.release
    if release is None:
        return None
    if not release.github or len(packages.names) != 1:
        raise ValueError("An official release requires one package identity")
    arch, supported = current_target()
    if not supported:
        raise ValueError("Unsupported Debian target")
    metadata = read_https(
        f"https://api.github.com/repos/{release.github}/releases/latest",
        DownloadPolicy(frozenset({"api.github.com"}), 1024 * 1024, 20, follow_redirects=False),
    )
    latest = GithubRelease.model_validate_json(metadata)
    tag = re.fullmatch(r"v?([0-9]+(?:\.[0-9]+){1,5})", latest.tag_name)
    if (
        latest.draft
        or latest.prerelease
        or not latest.published_at
        or not tag
        or arch not in release.asset
    ):
        raise ValueError("No stable official release for this architecture")
    version = tag[1]
    filename = release.asset[arch].replace("{version}", version)
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.+-]{1,180}\.deb", filename):
        raise ValueError("Invalid official Debian package name")
    url, expected = latest.asset(release.github, filename)
    content = read_https(url, DownloadPolicy(GITHUB_RELEASE_HOSTS, 350 * 1024 * 1024, 30))
    checksum = hashlib.sha256(content).hexdigest()
    if checksum != expected:
        raise ValueError("Release archive digest differs")
    path = stage / filename
    path.write_bytes(content)
    path.chmod(0o644)
    identity = query(
        [
            "/usr/bin/dpkg-deb",
            "--show",
            "--showformat=${Package}|${Architecture}|${Version}",
            str(path),
        ],
        resource_root(),
        stage=stage,
    )
    name, deb_arch, deb_version = identity.split("|")
    if name != packages.names[0] or deb_arch != arch or deb_version.partition("-")[0] != version:
        raise ValueError("Upstream archive identity differs")
    return path, checksum


UNIT_ROOTS = (
    Path("/etc/systemd/system"),
    Path("/run/systemd/system"),
    Path("/usr/lib/systemd/system"),
)


def unit_info(service: str) -> dict[str, str]:
    paths = {root / service for root in UNIT_ROOTS}
    for path in paths:
        if path.is_symlink():
            raise ValueError("A system unit link or administrator mask requires review")
        protected(path, directory=False)
    state = run_command(
        [
            "/usr/bin/systemctl",
            "--system",
            "--no-ask-password",
            "show",
            service,
            "--property=LoadState,ActiveState,FragmentPath",
        ],
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        check=False,
        timeout=10,
        discard_stderr=True,
    )
    facts = dict(line.split("=", 1) for line in state.stdout.splitlines() if "=" in line)
    if (
        state.returncode not in {0, 4}
        or facts.get("LoadState") not in {"loaded", "not-found"}
        or facts.get("ActiveState") not in {"inactive", "active"}
    ):
        raise ValueError("Cannot verify the system manager service state")
    fragment = facts.get("FragmentPath", "")
    if (fragment and Path(fragment) not in paths) or (
        facts["LoadState"] == "loaded" and not fragment
    ):
        raise ValueError("Unexpected system service fragment")
    return facts


def require_idle(packages: Packages) -> None:
    rule = packages.requires_idle
    if rule is None:
        return
    if unit_info(rule.service)["ActiveState"] != "inactive":
        raise ValueError("Disconnect the client and stop its active service before updating")
    for process in rule.processes:
        if (
            run_command(
                ["/usr/bin/pgrep", "-x", process], check=False, timeout=10, discard_output=True
            ).returncode
            != 1
        ):
            raise ValueError("Close the active application before updating")


def keyring(path: Path) -> None:
    try:
        protected(path, directory=False)
        if not path.is_file() or not path.stat().st_size:
            raise ValueError("Missing or empty keyring")
    except (OSError, ValueError) as exc:
        raise ValueError("The Debian archive keyring must be a protected regular file") from exc


def resolve(root: Path, stage: Path, folder: Path, packages: Packages) -> Plan:
    if not packages.names:
        return Plan(outcome=Outcome.BLOCKED, summary="This catalog permits only package removal")
    keyring(Path("/usr/share/keyrings/debian-archive-keyring.pgp"))
    opts = options(root, stage, folder, packages)
    for name in ("lists", "archives"):
        (stage / name).mkdir(mode=0o755)
    query(["/usr/bin/apt-get", *opts, "update", "--error-on=any"], root, 180)
    external = archive(stage, packages)
    targets = [str(external[0])] if external else packages.candidates()
    args = ["--no-remove", "--no-install-recommends", "install", "--", *targets]
    rows = closure(query(["/usr/bin/apt-get", *opts, "--simulate", *args], root))
    if any(name in rows and rows[name][0] is not None for name in packages.names):
        require_idle(packages)
    resources = []
    for name in sorted(set(rows) | set(packages.names)):
        before = installed(name)
        if name not in rows:
            if before is None:
                raise ValueError("Package was absent from the resolved transaction")
            resources.append(
                Resource(
                    kind="package",
                    identity=name,
                    ownership=ResourceOwnership.PREEXISTING
                    if name in packages.names
                    else ResourceOwnership.SHARED,
                    before=before,
                    after=before,
                )
            )
            continue
        old, new = rows[name]
        if old != before:
            raise ValueError("Package changed during resolution")
        if external and name in packages.names:
            hashes = {external[1]}
        else:
            metadata = query(["/usr/bin/apt-cache", *opts, "show", "--", f"{name}={new}"], root)
            hashes = set(re.findall(r"(?m)^SHA256: ([0-9a-f]{64})$", metadata))
        if len(hashes) != 1:
            raise ValueError("Missing or ambiguous package digest")
        resources.append(
            Resource(
                kind="package",
                identity=name,
                ownership=ResourceOwnership.CREATED if old is None else ResourceOwnership.MODIFIED,
                before=before,
                after=new,
                source_sha256=hashes.pop(),
                cleanup=old is None and name in packages.names,
            )
        )
    return Plan(
        summary="Install the reviewed signed Debian package closure",
        resources=resources,
        warnings=[
            "Installed packages and maintainer-script changes have limited rollback.",
            "Keep shared dependencies for other programs. Review APT autoremove separately.",
        ],
        outcome=Outcome.READY if rows else Outcome.NO_CHANGES,
    )


def package_plan(folder: Path) -> Plan:
    if not current_target()[1]:
        return Plan(
            outcome=Outcome.BLOCKED, summary="Requires supported Debian 13 and native architecture"
        )
    with tempfile.TemporaryDirectory(prefix="setup-system-apt-plan-") as directory:
        return resolve(
            resource_root(),
            Path(directory),
            folder,
            Packages.model_validate_json((folder / "apt.json").read_bytes()),
        )


def removal(root: Path, packages: Packages, selected: list[Resource]) -> Plan:
    if not current_target()[1]:
        return Plan(outcome=Outcome.BLOCKED, summary="Package removal requires supported Debian")
    rows = []
    for row in selected:
        if (
            row.kind != "package"
            or not row.cleanup
            or row.ownership != "created"
            or not packages.accepts_removal(row.identity)
            or row.before is None
            or row.effect != "remove"
        ):
            raise ValueError("Package removal lacks a reviewed native ownership record")
        version = installed(row.identity)
        if version is None:
            continue
        if version != row.before:
            raise ValueError("Package changed after installation")
        status = query(
            [
                "/usr/bin/dpkg-query",
                "-W",
                "-f=${db:Status-Want}|${Essential}|${Protected}|${Architecture}",
                "--",
                row.identity,
            ],
            root,
        ).split("|")
        if (
            len(status) != 4
            or status[0] != "install"
            or "yes" in status[1:3]
            or status[3] not in {current_target()[0], "all"}
        ):
            raise ValueError("Refusing a held, protected, essential or foreign package")
        rows.append(row)
    if not rows:
        return Plan(outcome=Outcome.NO_CHANGES, summary="Owned application packages are absent")
    names = {r.identity for r in rows}
    output = query(["/usr/bin/apt-get", "--simulate", "remove", "--", *sorted(names)], root)
    found = set()
    for line in output.splitlines():
        if not line.startswith(("Inst ", "Conf ", "Remv ", "Purg ")):
            continue
        match = re.fullmatch(r"Remv ([a-z0-9][a-z0-9+.-]*)(?: \[([^]]+)\])?", line)
        if not match or match[1] in found:
            raise ValueError("The removal would alter another package")
        found.add(match[1])
    if found != names:
        raise ValueError("The APT removal closure includes unowned packages")
    require_idle(packages)
    return Plan(
        summary="Remove the reviewed application packages",
        resources=sorted(rows, key=lambda r: r.identity),
        warnings=["Package removal preserves configuration and user data."],
    )


def uninstall_plan(folder: Path, retired_name: str | None = None) -> Plan:
    packages = Packages.model_validate_json((folder / "apt.json").read_bytes())
    if retired_name is not None and retired_name not in packages.retired_names:
        raise ValueError("Select a package from the protected retired list")
    selection = [
        Resource(
            kind="package",
            identity=r.identity,
            effect=ResourceEffect.REMOVE,
            ownership=ResourceOwnership.CREATED,
            before=r.after,
            cleanup=True,
        )
        for r in owned_resources()
        if r.kind == "package"
        and packages.accepts_removal(r.identity)
        and (retired_name is None or r.identity == retired_name)
        and r.ownership == "created"
        and r.cleanup
        and r.after is not None
    ]
    if not selection:
        return Plan(
            outcome=Outcome.NO_CHANGES,
            summary="No owned application packages to remove.",
        )
    return removal(resource_root(), packages, selection)


def verify_packages(plan: Plan) -> Result:
    good = bool(plan.resources) and all(
        (installed(row.identity) is None)
        if row.effect == "remove"
        else (row.after is not None and installed(row.identity) == row.after)
        for row in plan.resources
    )
    return Result.observed(
        outcome=Outcome.COMPLETE if good else Outcome.PARTIAL,
        message="Package versions verified" if good else "Debian package state differs",
        resources=plan.resources,
    )


def catalog_relative(plugin_id: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", plugin_id):
        raise ValueError("Invalid package catalog owner")
    package = "setup_plugin_" + plugin_id.replace("-", "_")
    return f"plugins/{plugin_id}/{package}/resources/apt.json"


def apply_packages(plugin_id: str, action_id: str, plan: Plan) -> None:
    approval = Approval.model_validate(
        {"plugin_id": plugin_id, "action_id": action_id, "plan": plan}
    )
    run_native_play(
        plugin_id,
        "@apt",
        {"approval": approval.model_dump(mode="json")},
        "sudo",
    )


def privileged(approval: Approval) -> None:
    if os.geteuid() != 0 or not current_target()[1]:
        raise ValueError("Unsupported package execution context")
    relative = catalog_relative(approval.plugin_id)
    source = INSTALLED / relative
    require_installed_resource(source, relative)
    packages = Packages.model_validate_json(source.read_bytes())
    if approval.action_id in SERVICE_TRANSITIONS:
        if (
            approval.plan != service_plan(approval.action_id, packages)
            or approval.plan.outcome != Outcome.READY
            or not packages.service
        ):
            raise ValueError("Service facts changed after confirmation")
        verb = SERVICE_TRANSITIONS[ServiceAction(approval.action_id)][0]
        rc = run_command(
            ["/usr/bin/systemctl", "--system", "--no-ask-password", verb, packages.service],
            cancellation=CancellationPolicy.SAFE_BOUNDARY,
            check=False,
            discard_output=True,
        )
        if (
            rc.returncode
            or service_plan(approval.action_id, packages).outcome != Outcome.NO_CHANGES
        ):
            raise ValueError("System service transition requires inspection")
        return
    with tempfile.TemporaryDirectory(prefix="setup-system-pkg-", dir="/run") as directory:
        stage = Path(directory)
        os.chmod(stage, 0o711)
        removing = approval.action_id in REMOVALS
        if approval.action_id is PackageAction.REMOVE_RETIRED:
            packages.check_retired_selection(approval.plan.resources)
        confirmed = (
            removal(INSTALLED, packages, approval.plan.resources)
            if removing
            else resolve(INSTALLED, stage, source.parent, packages)
        )
        if confirmed != approval.plan or confirmed.outcome != Outcome.READY:
            raise ValueError("Package plan changed at privilege boundary")
        receipt = stage / "approval.json"
        receipt.write_text(approval.model_dump_json())
        receipt.chmod(0o600)
        hook = f"/usr/bin/python3 -I -B -m setup_plugin_debian.adapters.apt check {stage}"
        if removing:
            subprocess.run(
                [
                    "/usr/bin/apt-get",
                    "-o",
                    "DPkg::Pre-Install-Pkgs::=" + hook,
                    "-o",
                    "DPkg::Tools::Options::/usr/bin/python3::Version=3",
                    "--assume-yes",
                    "remove",
                    "--",
                    *[r.identity for r in confirmed.resources],
                ],
                check=True,
                env=apt_environment(INSTALLED),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        targets = (
            [str(p) for p in stage.glob("*.deb")] if packages.release else packages.candidates()
        )
        if len(targets) != len(packages.names):
            raise ValueError("Reviewed package archive unavailable")
        args = [
            "/usr/bin/apt-get",
            *options(INSTALLED, stage, source.parent, packages),
            "-o",
            "DPkg::Pre-Install-Pkgs::=" + hook,
            "-o",
            "DPkg::Tools::Options::/usr/bin/python3::Version=3",
            "--assume-yes",
            "--no-remove",
            "--no-install-recommends",
            "install",
            "--",
            *targets,
        ]
        with defer_services(Path("/usr/sbin/policy-rc.d")):
            subprocess.run(
                args,
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd="/",
                env=apt_environment(INSTALLED),
            )


def check(stage: Path) -> None:
    if (
        os.geteuid() != 0
        or stage.parent != Path("/run")
        or not re.fullmatch(r"setup-system-pkg-[a-zA-Z0-9_-]+", stage.name)
    ):
        raise ValueError("Unapproved package staging path")
    receipt = stage / "approval.json"
    protected(receipt, directory=False)
    approval = Approval.model_validate_json(receipt.read_bytes())
    relative = catalog_relative(approval.plugin_id)
    package_file = INSTALLED / relative
    require_installed_resource(package_file, relative)
    packages = Packages.model_validate_json(package_file.read_bytes())
    if any(
        r.identity in packages.names and r.before is not None and r.before != r.after
        for r in approval.plan.resources
    ):
        require_idle(packages)
    if approval.action_id is PackageAction.REMOVE_RETIRED:
        packages.check_retired_selection(approval.plan.resources)
    verify_hook(stage, approval)
    if approval.action_id in REMOVALS and packages.removal_guard:
        if any(not packages.accepts_removal(r.identity) for r in approval.plan.resources):
            raise ValueError("Removal guard belongs to a different package")
        guard = package_file.parent.parent / "adapters" / packages.removal_guard
        require_installed_resource(guard, guard.relative_to(INSTALLED).as_posix())
        run_command(
            ["/usr/bin/python3", "-I", "-B", str(guard)],
            timeout=30,
            stdin=subprocess.DEVNULL,
            env=apt_environment(INSTALLED),
            cwd="/",
            discard_output=True,
        )


def verify_hook(stage: Path, approval: Approval) -> None:
    expected = {r.identity: r for r in approval.plan.resources if r.before != r.after}
    stream = iter(sys.stdin)
    if next(stream, "").strip() != "VERSION 3":
        raise ValueError("APT must support its version 3 pre-install hook")
    for line in stream:
        if not line.strip():
            break
    seen: set[tuple[str, str]] = set()
    arch, supported = current_target()
    if not supported:
        raise ValueError("Unsupported native package architecture")
    for line in stream:
        fields = line.rstrip().split(maxsplit=8)
        if len(fields) != 9:
            raise ValueError("Invalid package action")
        name, old, old_arch, _, direction, selected, native_arch, _, action = fields
        resource = expected.get(name)
        if approval.action_id in REMOVALS:
            if (
                resource is None
                or resource.effect != "remove"
                or not resource.before
                or action != "**REMOVE**"
                or old != resource.before
                or selected != "-"
                or old_arch not in {arch, "all"}
                or (name, "remove") in seen
            ):
                raise ValueError("Unapproved dpkg removal")
            seen.add((name, "remove"))
            continue
        if (
            resource is None
            or resource.after is None
            or direction not in {"<", "="}
            or native_arch not in {arch, "all"}
        ):
            raise ValueError("Unapproved dpkg action")
        version, checksum = resource.after, resource.source_sha256
        if not checksum:
            raise ValueError("Missing confirmed archive digest")
        phase = "configure" if action == "**CONFIGURE**" else "install"
        if (
            selected != version
            or (name, phase) in seen
            or (
                phase == "configure"
                and (old != version or direction != "=" or old_arch != native_arch)
            )
            or (phase == "install" and old != "-" and old_arch not in {native_arch, "all"})
        ):
            raise ValueError("Changed version or duplicate dpkg action")
        if phase == "install":
            path = Path(action)
            if (
                old != (resource.before or "-")
                or not (path.is_relative_to(stage / "archives") or path.parent == stage)
                or path.suffix != ".deb"
            ):
                raise ValueError("Unapproved dpkg input")
            protected(path, directory=False)
            with path.open("rb") as archive:
                if hashlib.file_digest(archive, "sha256").hexdigest() != checksum:
                    raise ValueError("Package archive no longer matches its signed index")
        seen.add((name, phase))
    if seen != {
        (name, phase)
        for name in expected
        for phase in (("remove",) if approval.action_id in REMOVALS else ("configure", "install"))
    }:
        raise ValueError("Actual dpkg package closure differs")


def service_plan(action: str, packages: Packages) -> Plan:
    if not packages.service or action not in SERVICE_TRANSITIONS:
        raise ValueError("Unapproved service action")
    facts = unit_info(packages.service)
    if facts["LoadState"] != "loaded":
        return Plan(
            outcome=Outcome.BLOCKED, summary="Install and review the protected system unit first"
        )
    active = facts["ActiveState"]
    desired = SERVICE_TRANSITIONS[ServiceAction(action)][1]
    return Plan(
        outcome=Outcome.NO_CHANGES if active == desired else Outcome.READY,
        summary=f"Set system unit {packages.service} to {desired}",
        resources=[
            Resource(
                kind="service",
                identity="system/" + packages.service,
                ownership=ResourceOwnership.MODIFIED,
                before=active,
                after=desired,
            )
        ],
        warnings=["Review active workloads before changing this system service."],
    )


if __name__ == "__main__":
    if sys.argv[1:] == ["apply"]:
        raw = sys.stdin.buffer.read(262145)
        if len(raw) > 262144:
            raise ValueError("Package approval exceeds request size limit")
        privileged(Authorization.model_validate_json(raw).approval)
    elif len(sys.argv) == 3 and sys.argv[1] == "check":
        check(Path(sys.argv[2]))
    else:
        sys.exit(2)
