"""Defer vendor manager commands during package apply, then activate only happd."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from setup_system import installation

PackageFixture = tuple[Path, installation.TargetUser, Path, installation.SystemPaths]
RELEASE = {"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"}


def read_only_reply(argv: list[str]) -> subprocess.CompletedProcess[str]:
    if argv[0] == "/usr/bin/dpkg-query":
        return subprocess.CompletedProcess(argv, 1, "", "")
    if argv[0] == "/usr/bin/dpkg-deb":
        return subprocess.CompletedProcess(
            argv, 0, "Package: happ\nVersion: 4.5.4-333\nArchitecture: amd64\n", ""
        )
    if "--version" in argv:
        return subprocess.CompletedProcess(argv, 0, "systemd 257 (257.9-1~deb13u1)\n", "")
    if "--property=Version" in argv:
        return subprocess.CompletedProcess(argv, 0, "257.9-1~deb13u1\n", "")
    return subprocess.CompletedProcess(argv, 0, "", "")


def test_offline_apt_defers_direct_vendor_systemctl_commands(
    package_fixture: PackageFixture,
) -> None:
    root, target, happ, paths = package_fixture
    observed: list[str] = []

    def execute(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        environment = options["env"]
        assert isinstance(environment, dict)
        if "--no-download" in argv:
            assert environment.get("SYSTEMD_OFFLINE") == "1"
            assert paths.policy.read_bytes() == installation.DEFER_POLICY
            observed.append("offline-package-apply")
        else:
            assert "SYSTEMD_OFFLINE" not in environment
        return read_only_reply(argv)

    with installation.prepare_packages(
        root,
        target=target,
        happ=happ,
        proxy=None,
        paths=paths,
        run=execute,
        release=RELEASE,
        architecture="amd64",
    ) as prepared:
        prepared.install()
    assert observed == ["offline-package-apply"]
    assert not paths.policy.exists()


@pytest.mark.parametrize("already_active", [False, True], ids=["first-start", "preserve-active"])
def test_vendor_happ_activation_occurs_after_packages_and_temporary_network_close(
    package_fixture: PackageFixture,
    already_active: bool,
) -> None:
    root, target, happ, paths = package_fixture
    active = already_active
    installed = False
    network_closed = False
    manager_calls: list[list[str]] = []

    def execute(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        nonlocal installed, active
        if "--no-download" in argv:
            assert network_closed
            installed = True
        if argv[0] == "/usr/bin/systemctl" and not (
            "--version" in argv or "--property=Version" in argv
        ):
            assert installed and network_closed
            environment = options["env"]
            assert isinstance(environment, dict) and "SYSTEMD_OFFLINE" not in environment
            manager_calls.append(argv)
            assert not {"restart", "stop", "enable", "unmask", "mask"} & set(argv)
            if "is-active" in argv:
                return subprocess.CompletedProcess(argv, 0 if active else 3, "", "")
            if "start" in argv:
                assert argv[-1] == "happd.service"
                active = True
        return read_only_reply(argv)

    with installation.prepare_packages(
        root,
        target=target,
        happ=happ,
        proxy=None,
        paths=paths,
        run=execute,
        release=RELEASE,
        architecture="amd64",
    ) as prepared:
        with pytest.raises(RuntimeError, match=r"install|package"):
            prepared.activate_happ()
        network_closed = True
        prepared.install()
        prepared.activate_happ()
        before = list(manager_calls)
        prepared.activate_happ()
        assert manager_calls == before
        assert sum("start" in call for call in manager_calls) == (0 if already_active else 1)
        assert sum("daemon-reload" in call for call in manager_calls) == 1
        assert any("is-active" in call for call in manager_calls)
        state = json.loads(paths.journal.read_text())
        assert state["status"] == "system-complete"
        assert state["completed"][-1] == "happ-service"
    with pytest.raises(RuntimeError, match=r"closed|active"):
        prepared.activate_happ()


@pytest.mark.parametrize("when", ["prepare", "install", "activate"])
def test_administrator_masks_are_never_overwritten_or_unmasked(
    package_fixture: PackageFixture,
    tmp_path: Path,
    when: str,
) -> None:
    root, target, happ, paths = package_fixture
    mask = tmp_path / "admin-systemd/happd.service"
    mask.parent.mkdir(mode=0o700)
    paths = replace(paths, happ_unit=mask)
    apt_installs: list[list[str]] = []
    manager_actions: list[list[str]] = []

    def execute(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        if "--no-download" in argv:
            apt_installs.append(argv)
        if argv[0] == "/usr/bin/systemctl" and not (
            "--version" in argv or "--property=Version" in argv
        ):
            manager_actions.append(argv)
        return read_only_reply(argv)

    if when == "prepare":
        mask.symlink_to("/dev/null")
    with pytest.raises(ValueError, match=r"masked|administrator"):
        with installation.prepare_packages(
            root,
            target=target,
            happ=happ,
            proxy=None,
            paths=paths,
            run=execute,
            release=RELEASE,
            architecture="amd64",
        ) as prepared:
            if when == "install":
                mask.symlink_to("/dev/null")
            prepared.install()
            if when == "activate":
                mask.symlink_to("/dev/null")
            prepared.activate_happ()
    assert mask.is_symlink() and mask.readlink() == Path("/dev/null")
    assert not manager_actions
    assert len(apt_installs) == (1 if when == "activate" else 0)


@pytest.mark.parametrize("failure", ["start", "verify"])
def test_failed_happ_activation_records_a_failed_required_stage(
    package_fixture: PackageFixture,
    failure: str,
) -> None:
    root, target, happ, paths = package_fixture

    def execute(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        if argv[0] == "/usr/bin/systemctl":
            if "start" in argv and failure == "start":
                raise subprocess.CalledProcessError(1, argv, stderr="fixture helper start denied")
            if "is-active" in argv:
                return subprocess.CompletedProcess(argv, 3, "", "")
        return read_only_reply(argv)

    with pytest.raises(RuntimeError, match=r"happd\.service"):
        with installation.prepare_packages(
            root,
            target=target,
            happ=happ,
            proxy=None,
            paths=paths,
            run=execute,
            release=RELEASE,
            architecture="amd64",
        ) as prepared:
            prepared.install()
            prepared.activate_happ()
    state = json.loads(paths.journal.read_text())
    assert state["status"] == "failed" and state["stage"] == "happ-service"
    assert "packages" in state["completed"] and "happ-service" not in state["completed"]
    assert paths.backports.is_file() and not paths.policy.exists()


@pytest.mark.parametrize("failure", ["missing-client", "unreviewed-version", "not-systemd"])
def test_missing_reviewed_systemd_manager_stops_before_persistent_or_staging_writes(
    package_fixture: PackageFixture,
    failure: str,
) -> None:
    root, target, happ, paths = package_fixture

    def execute(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        assert argv[0] == "/usr/bin/systemctl"
        if failure == "missing-client":
            raise FileNotFoundError("fixture has no systemctl")
        if "--version" in argv and failure == "unreviewed-version":
            return subprocess.CompletedProcess(argv, 0, "systemd 256\n", "")
        if "--property=Version" in argv and failure == "not-systemd":
            return subprocess.CompletedProcess(argv, 1, "", "fixture init is not systemd")
        return read_only_reply(argv)

    with pytest.raises(ValueError, match=r"systemd|manager|systemctl"):
        with installation.prepare_packages(
            root,
            target=target,
            happ=happ,
            proxy=None,
            paths=paths,
            run=execute,
            release=RELEASE,
            architecture="amd64",
        ):
            pytest.fail("Unsupported manager reached package preparation")
    assert not paths.journal.exists() and not list(paths.runtime.iterdir())


@pytest.mark.parametrize("when", ["prepare", "install", "activate"])
@pytest.mark.parametrize("unit_name", ["happ_unit", "happ_runtime_unit"])
@pytest.mark.parametrize("unsafe", ["unit", "parent"])
def test_uncontrolled_happ_unit_or_parent_stops_before_manager_actions(
    package_fixture: PackageFixture,
    when: str,
    unit_name: str,
    unsafe: str,
) -> None:
    root, target, happ, paths = package_fixture
    unit = getattr(paths, unit_name)
    unit.parent.mkdir(parents=True, mode=0o700)
    unit.write_text("Synthetic administrator unit: preserve these bytes\n")
    unit.chmod(0o600)
    original = unit.read_bytes()
    apt_installs: list[list[str]] = []
    manager_actions: list[list[str]] = []

    def make_unsafe() -> None:
        (unit if unsafe == "unit" else unit.parent).chmod(0o666 if unsafe == "unit" else 0o777)

    def execute(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        if "--no-download" in argv:
            apt_installs.append(argv)
        if argv[0] == "/usr/bin/systemctl" and not (
            "--version" in argv or "--property=Version" in argv
        ):
            manager_actions.append(argv)
        return read_only_reply(argv)

    if when == "prepare":
        make_unsafe()
    try:
        with pytest.raises(ValueError, match=r"controlled|System|parent|unit"):
            with installation.prepare_packages(
                root,
                target=target,
                happ=happ,
                proxy=None,
                paths=paths,
                run=execute,
                release=RELEASE,
                architecture="amd64",
            ) as prepared:
                if when == "install":
                    make_unsafe()
                prepared.install()
                if when == "activate":
                    make_unsafe()
                prepared.activate_happ()
        assert unit.read_bytes() == original
        assert not manager_actions
        assert len(apt_installs) == (1 if when == "activate" else 0)
        assert (unit if unsafe == "unit" else unit.parent).stat().st_mode & 0o022
    finally:
        unit.chmod(0o600)
        unit.parent.chmod(0o700)
