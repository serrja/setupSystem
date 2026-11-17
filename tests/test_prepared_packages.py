"""Download through the approved proxy, then install inside an offline process namespace."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from setup_system import installation

RELEASE = {"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"}
PackageFixture = tuple[Path, installation.TargetUser, Path, installation.SystemPaths]
PROXY = "http://127.0.0.1:18080"
SIMULATION = (
    "Inst hyprland (0.55.2+ds-1~bpo13+1 Debian:trixie-backports [amd64])\n"
    "Inst happ (4.5.4-333 local-deb [amd64])\n"
    "Inst fixture-dependency (9.9 Debian:trixie [amd64])\n"
)


def metadata(argv: list[str], *, architecture: str = "amd64") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        argv, 0, f"Package: happ\nVersion: 4.5.4-333\nArchitecture: {architecture}\n", ""
    )


def test_downloads_precede_offline_apply_and_keep_one_verified_private_selection(
    package_fixture: PackageFixture,
) -> None:
    root, target, happ, paths = package_fixture
    original = happ.read_bytes()
    closed = False
    apt_calls: list[list[str]] = []
    staged: list[Path] = []

    def execute(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        if argv[0] == "/usr/bin/dpkg-query":
            return subprocess.CompletedProcess(argv, 1, "", "")
        if argv[0] == "/usr/bin/systemctl":
            text = (
                "systemd 257 (257.9-1~deb13u1)\n"
                if "--version" in argv
                else ("257.9-1~deb13u1\n" if "--property=Version" in argv else "")
            )
            return subprocess.CompletedProcess(argv, 0, text, "")
        environment = options["env"]
        assert isinstance(environment, dict)
        assert not {"APT_CONFIG", "HTTP_PROXY", "HTTPS_PROXY", "GH_TOKEN"} & environment.keys()
        if argv[:1] == ["/usr/bin/dpkg-deb"]:
            artifact = Path(argv[2])
            staged.append(artifact)
            assert artifact != happ and artifact.read_bytes() == original
            assert artifact.stat().st_uid == os.geteuid()
            assert artifact.stat().st_mode & 0o777 == 0o600
            assert artifact.parent.stat().st_mode & 0o777 == 0o700
            return metadata(argv)
        if "/usr/bin/apt-get" not in argv:
            assert argv == ["/usr/bin/unshare", "--net", "--", "/usr/bin/true"]
            return subprocess.CompletedProcess(argv, 0, "", "")
        apt_calls.append(argv)
        if "--no-download" in argv:
            assert closed
            assert argv[:4] == ["/usr/bin/unshare", "--net", "--", "/usr/bin/apt-get"]
            assert not any(PROXY in argument for argument in argv)
            assert paths.policy.read_bytes() == installation.DEFER_POLICY
            assert "--download-only" not in argv
        else:
            assert not closed
            assert f"Acquire::http::Proxy={PROXY}" in argv
            assert f"Acquire::https::Proxy={PROXY}" in argv
            assert not paths.policy.exists()
        return subprocess.CompletedProcess(argv, 0, SIMULATION if "--simulate" in argv else "", "")

    with installation.prepare_packages(
        root,
        target=target,
        happ=happ,
        proxy=PROXY,
        run=execute,
        paths=paths,
        release=RELEASE,
        architecture="amd64",
    ) as prepared:
        assert len(apt_calls) == 3
        assert "update" in apt_calls[0]
        assert "--simulate" in apt_calls[1]
        assert "--download-only" in apt_calls[2] and "--yes" in apt_calls[2]
        assert not paths.backports.exists()
        assert json.loads(paths.journal.read_text())["status"] == "prepared"
        assert staged[0].is_file()
        closed = True
        prepared.install()
        prepared.install()
        assert len(apt_calls) == 4
        download = apt_calls[2][apt_calls[2].index("install") + 1 :]
        offline = apt_calls[3][apt_calls[3].index("install") + 1 :]
        assert download == offline
        assert "fixture-dependency=9.9" in offline
        assert str(staged[0]) in offline
        assert all("--no-remove" in call for call in apt_calls[1:])
        assert all("--no-install-recommends" in call for call in apt_calls[1:])
        assert json.loads(paths.journal.read_text())["status"] == "packages-complete"
        prepared.activate_happ()
        prepared.activate_happ()
        state = json.loads(paths.journal.read_text())
        assert state["status"] == "system-complete"
        assert state["completed"] == [
            "metadata",
            "resolution",
            "downloads",
            "packages",
            "source",
            "happ-service",
        ]
    assert not list(paths.runtime.iterdir())
    assert happ.read_bytes() == original and happ.stat().st_uid == target.uid
    assert not paths.policy.exists()
    with pytest.raises(RuntimeError, match=r"active|closed"):
        prepared.install()


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
def test_root_reverifies_happ_metadata_against_target_architecture(
    package_fixture: PackageFixture, architecture: str
) -> None:
    root, target, happ, paths = package_fixture

    def execute(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        if argv[0] == "/usr/bin/dpkg-query":
            return subprocess.CompletedProcess(argv, 1, "", "")
        if argv[0] == "/usr/bin/systemctl":
            text = (
                "systemd 257 (257.9-1~deb13u1)\n"
                if "--version" in argv
                else ("257.9-1~deb13u1\n" if "--property=Version" in argv else "")
            )
            return subprocess.CompletedProcess(argv, 0, text, "")
        if argv[0] == "/usr/bin/dpkg-deb":
            return metadata(argv, architecture=architecture)
        return subprocess.CompletedProcess(argv, 0, "", "")

    with installation.prepare_packages(
        root,
        target=target,
        happ=happ,
        proxy=None,
        run=execute,
        paths=paths,
        release=RELEASE,
        architecture=architecture,
    ) as prepared:
        prepared.install()
        prepared.activate_happ()
    assert json.loads(paths.journal.read_text())["status"] == "system-complete"


@pytest.mark.parametrize("kind", ["digest", "name", "version", "architecture"])
def test_happ_failure_stops_before_any_apt_or_persistent_system_write(
    package_fixture: PackageFixture, kind: str
) -> None:
    root, target, happ, paths = package_fixture
    if kind == "digest":
        happ.write_bytes(b"corrupted archive")
    calls: list[list[str]] = []

    def execute(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        if argv[0] == "/usr/bin/dpkg-query":
            return subprocess.CompletedProcess(argv, 1, "", "")
        if argv[0] == "/usr/bin/systemctl":
            text = (
                "systemd 257 (257.9-1~deb13u1)\n"
                if "--version" in argv
                else ("257.9-1~deb13u1\n" if "--property=Version" in argv else "")
            )
            return subprocess.CompletedProcess(argv, 0, text, "")
        calls.append(argv)
        assert "/usr/bin/apt-get" not in argv
        if argv[0] == "/usr/bin/dpkg-deb":
            fields = ["happ", "4.5.4-333", "amd64"]
            fields[["name", "version", "architecture"].index(kind)] = "unexpected"
            return subprocess.CompletedProcess(
                argv,
                0,
                "\n".join(
                    f"{label}: {value}"
                    for label, value in zip(
                        ("Package", "Version", "Architecture"), fields, strict=True
                    )
                )
                + "\n",
                "",
            )
        return subprocess.CompletedProcess(argv, 0, "", "")

    with pytest.raises(ValueError, match=r"Happ|digest|metadata"):
        with installation.prepare_packages(
            root,
            target=target,
            happ=happ,
            proxy=PROXY,
            run=execute,
            paths=paths,
            release=RELEASE,
            architecture="amd64",
        ):
            pytest.fail("Invalid Happ archive reached prepared packages")
    assert not paths.journal.exists() and not paths.backports.exists()
    assert not list(paths.runtime.iterdir())
    if kind == "digest":
        assert not any(call[0] == "/usr/bin/dpkg-deb" for call in calls)


@pytest.mark.parametrize("kind", ["symlink", "outside-home", "owner", "oversize", "special"])
def test_unsafe_user_artifacts_fail_closed_before_apt(
    package_fixture: PackageFixture, kind: str
) -> None:
    root, target, happ, paths = package_fixture
    if kind == "symlink":
        link = happ.with_name("linked.deb")
        link.symlink_to(happ)
        happ = link
    elif kind == "outside-home":
        outside = paths.runtime / "outside.deb"
        outside.write_bytes(happ.read_bytes())
        happ = outside
    elif kind == "owner":
        target = replace(target, uid=target.uid + 1)
    elif kind == "oversize":
        with happ.open("r+b") as archive:
            archive.truncate(200 * 1024 * 1024 + 1)
    else:
        happ.unlink()
        os.mkfifo(happ)

    def forbidden(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        if argv[0] == "/usr/bin/dpkg-query":
            return subprocess.CompletedProcess(argv, 1, "", "")
        if argv[0] == "/usr/bin/systemctl":
            text = (
                "systemd 257 (257.9-1~deb13u1)\n"
                if "--version" in argv
                else ("257.9-1~deb13u1\n" if "--property=Version" in argv else "")
            )
            return subprocess.CompletedProcess(argv, 0, text, "")
        assert "/usr/bin/apt-get" not in argv
        return subprocess.CompletedProcess(argv, 0, "", "")

    before_runtime = set(paths.runtime.iterdir())
    with pytest.raises(ValueError):
        with installation.prepare_packages(
            root,
            target=target,
            happ=happ,
            proxy=None,
            run=forbidden,
            paths=paths,
            release=RELEASE,
            architecture="amd64",
        ):
            pytest.fail("Unsafe Happ artifact reached package preparation")
    assert not paths.journal.exists() and not paths.backports.exists()
    assert set(paths.runtime.iterdir()) == before_runtime


@pytest.mark.parametrize(
    "proxy",
    [
        "https://127.0.0.1:18080",
        "http://user:private@127.0.0.1:18080",
        "http://127.0.0.1:80",
        "http://example.invalid:18080",
        "http://localhost:18080",
        "http://127.0.0.1:18080/path",
        "http://127.0.0.1:18080?query",
        "http://[::1]:18080#fragment",
    ],
)
def test_only_credential_free_literal_loopback_http_high_port_is_accepted(
    package_fixture: PackageFixture, proxy: str
) -> None:
    root, target, happ, paths = package_fixture

    def forbidden(*args: object, **options: object) -> subprocess.CompletedProcess[str]:
        pytest.fail("Invalid proxy reached a process or a package stage")

    with pytest.raises(ValueError, match="proxy"):
        with installation.prepare_packages(
            root,
            target=target,
            happ=happ,
            proxy=proxy,
            run=forbidden,
            paths=paths,
            release=RELEASE,
            architecture="amd64",
        ):
            pytest.fail("Invalid proxy reached prepared packages")
    assert not paths.journal.exists() and not list(paths.runtime.iterdir())


def test_missing_offline_namespace_support_fails_before_staging_or_journal(
    package_fixture: PackageFixture,
) -> None:
    root, target, happ, paths = package_fixture

    def unavailable(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        if argv[0] == "/usr/bin/dpkg-query":
            return subprocess.CompletedProcess(argv, 1, "", "")
        if argv[0] == "/usr/bin/systemctl":
            text = (
                "systemd 257 (257.9-1~deb13u1)\n"
                if "--version" in argv
                else ("257.9-1~deb13u1\n" if "--property=Version" in argv else "")
            )
            return subprocess.CompletedProcess(argv, 0, text, "")
        assert argv == ["/usr/bin/unshare", "--net", "--", "/usr/bin/true"]
        raise subprocess.CalledProcessError(1, argv)

    with pytest.raises(ValueError, match=r"offline|namespace|unshare"):
        with installation.prepare_packages(
            root,
            target=target,
            happ=happ,
            proxy=None,
            run=unavailable,
            paths=paths,
            release=RELEASE,
            architecture="amd64",
        ):
            pytest.fail("Offline namespace failure reached prepared packages")
    assert not paths.journal.exists() and not list(paths.runtime.iterdir())


@pytest.mark.parametrize("failure", ["download", "offline-install", "prefetch", "removal"])
def test_failure_records_the_actual_stage_and_never_claims_system_completion(
    package_fixture: PackageFixture, failure: str
) -> None:
    root, target, happ, paths = package_fixture
    apt_calls: list[list[str]] = []

    def execute(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        if argv[0] == "/usr/bin/dpkg-query":
            return subprocess.CompletedProcess(argv, 1, "", "")
        if argv[0] == "/usr/bin/systemctl":
            text = (
                "systemd 257 (257.9-1~deb13u1)\n"
                if "--version" in argv
                else ("257.9-1~deb13u1\n" if "--property=Version" in argv else "")
            )
            return subprocess.CompletedProcess(argv, 0, text, "")
        if argv[0] == "/usr/bin/dpkg-deb":
            return metadata(argv)
        if "/usr/bin/apt-get" in argv:
            apt_calls.append(argv)
            if (failure == "download" and "--download-only" in argv) or (
                failure == "offline-install" and "--no-download" in argv
            ):
                raise subprocess.CalledProcessError(100, argv)
            if failure == "removal" and "--simulate" in argv:
                return subprocess.CompletedProcess(argv, 0, "Remv existing-package [1]\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    with pytest.raises((subprocess.CalledProcessError, RuntimeError)):
        with installation.prepare_packages(
            root,
            target=target,
            happ=happ,
            proxy=PROXY,
            run=execute,
            paths=paths,
            release=RELEASE,
            architecture="amd64",
        ) as prepared:
            if failure == "prefetch":
                raise RuntimeError("Fixture user prefetch failure")
            prepared.install()
    state = json.loads(paths.journal.read_text())
    assert state["status"] == "failed"
    assert (
        state["stage"]
        == {
            "download": "downloads",
            "offline-install": "packages",
            "prefetch": "user-prefetch",
            "removal": "resolution",
        }[failure]
    )
    assert not paths.backports.exists() and not paths.policy.exists()
    assert not list(paths.runtime.iterdir())
    if failure != "offline-install":
        assert not any("--no-download" in call for call in apt_calls)


def test_offline_apply_uses_private_happ_after_user_session_artifact_is_deleted(
    package_fixture: PackageFixture,
) -> None:
    root, target, happ, paths = package_fixture
    copied: list[Path] = []

    def execute(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        if argv[0] == "/usr/bin/dpkg-query":
            return subprocess.CompletedProcess(argv, 1, "", "")
        if argv[0] == "/usr/bin/systemctl":
            text = (
                "systemd 257 (257.9-1~deb13u1)\n"
                if "--version" in argv
                else ("257.9-1~deb13u1\n" if "--property=Version" in argv else "")
            )
            return subprocess.CompletedProcess(argv, 0, text, "")
        if argv[0] == "/usr/bin/dpkg-deb":
            copied.append(Path(argv[2]))
            return metadata(argv)
        if "--download-only" in argv or "--no-download" in argv:
            cached = copied[0].parent / "archives/happ_4.5.4-333_amd64.deb"
            assert cached.is_absolute() and cached.is_file()
            assert cached.samefile(copied[0])
            assert cached.read_bytes() == copied[0].read_bytes()
            assert cached.stat().st_uid == os.getuid()
            assert cached.stat().st_mode & 0o777 == 0o644
        if "--no-download" in argv:
            assert not happ.exists()
            assert copied[0].is_file() and str(copied[0]) in argv
        return subprocess.CompletedProcess(argv, 0, "", "")

    with installation.prepare_packages(
        root,
        target=target,
        happ=happ,
        proxy=PROXY,
        run=execute,
        paths=paths,
        release=RELEASE,
        architecture="amd64",
    ) as prepared:
        happ.unlink()
        prepared.install()
        prepared.activate_happ()
    assert not copied[0].exists()
    assert json.loads(paths.journal.read_text())["status"] == "system-complete"


def test_malformed_happ_manifest_fails_before_a_process_or_write(
    package_fixture: PackageFixture,
) -> None:
    root, target, happ, paths = package_fixture
    (root / "manifests/happ.toml").write_text('version = "4.5.4"\n')

    def forbidden(*args: object, **options: object) -> subprocess.CompletedProcess[str]:
        pytest.fail("Malformed Happ pin reached a process")

    with pytest.raises(ValueError, match="Happ"):
        with installation.prepare_packages(
            root,
            target=target,
            happ=happ,
            proxy=PROXY,
            run=forbidden,
            paths=paths,
            release=RELEASE,
            architecture="amd64",
        ):
            pytest.fail("Malformed Happ manifest reached prepared packages")
    assert not paths.journal.exists() and not list(paths.runtime.iterdir())


@pytest.mark.parametrize(
    "simulation",
    [
        "Inst hyprland (0.0.1 unexpected [amd64])\n",
        "Inst happ (0.0.1 unexpected [amd64])\n",
        "Inst malformed-entry\n",
    ],
)
def test_changed_or_unparseable_package_selection_stops_before_downloads(
    package_fixture: PackageFixture,
    simulation: str,
) -> None:
    root, target, happ, paths = package_fixture

    def execute(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        if argv[0] == "/usr/bin/dpkg-query":
            return subprocess.CompletedProcess(argv, 1, "", "")
        if argv[0] == "/usr/bin/systemctl":
            text = (
                "systemd 257 (257.9-1~deb13u1)\n"
                if "--version" in argv
                else ("257.9-1~deb13u1\n" if "--property=Version" in argv else "")
            )
            return subprocess.CompletedProcess(argv, 0, text, "")
        if argv[0] == "/usr/bin/dpkg-deb":
            return metadata(argv)
        assert "--download-only" not in argv and "--no-download" not in argv
        return subprocess.CompletedProcess(argv, 0, simulation if "--simulate" in argv else "", "")

    with pytest.raises(RuntimeError, match=r"APT|Happ"):
        with installation.prepare_packages(
            root,
            target=target,
            happ=happ,
            proxy=PROXY,
            run=execute,
            paths=paths,
            release=RELEASE,
            architecture="amd64",
        ):
            pytest.fail("Unverified APT selection reached downloads")
    state = json.loads(paths.journal.read_text())
    assert state["status"] == "failed" and state["stage"] == "resolution"


@pytest.mark.parametrize("mask", [0o002, 0o077, 0o777])
def test_only_verified_public_cache_permissions_are_relaxed_for_apt_acquisition(
    package_fixture: PackageFixture,
    mask: int,
) -> None:
    root, target, happ, paths = package_fixture
    original = happ.read_bytes()
    verified: list[Path] = []
    apt_seen: list[str] = []

    def execute(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        if argv[0] == "/usr/bin/dpkg-query":
            return subprocess.CompletedProcess(argv, 1, "", "")
        if argv[0] == "/usr/bin/systemctl":
            text = (
                "systemd 257 (257.9-1~deb13u1)\n"
                if "--version" in argv
                else ("257.9-1~deb13u1\n" if "--property=Version" in argv else "")
            )
            return subprocess.CompletedProcess(argv, 0, text, "")
        if argv[0] == "/usr/bin/dpkg-deb":
            candidate = Path(argv[2])
            assert candidate.read_bytes() == original
            assert candidate.stat().st_mode & 0o777 == 0o600
            assert candidate.parent.stat().st_mode & 0o777 == 0o700
            assert candidate.stat().st_uid == os.geteuid()
            verified.append(candidate)
            return metadata(argv)
        if "/usr/bin/apt-get" in argv:
            assert verified, "APT acquisition started before Happ verification"
            candidate = verified[0]
            stage = candidate.parent
            assert candidate.read_bytes() == original
            assert candidate.stat().st_mode & 0o777 == 0o644
            assert candidate.stat().st_uid == os.geteuid()
            assert stage.stat().st_mode & 0o777 == 0o755
            if not apt_seen:
                source = Path(
                    next(
                        value.split("=", 1)[1]
                        for value in argv
                        if value.startswith("Dir::Etc::sourcelist=")
                    )
                )
                assert source.parent == stage and source.stat().st_mode & 0o777 == 0o644
                assert f"Signed-By: {paths.keyring}" in source.read_text()
                for name in ("lists", "lists/partial", "archives", "archives/partial"):
                    directory = stage / name
                    assert directory.is_dir() and directory.stat().st_mode & 0o777 == 0o755
                    if not name.endswith("/partial"):
                        assert directory.stat().st_uid == os.geteuid()
            assert paths.journal.stat().st_mode & 0o777 == 0o600
            assert paths.journal.parent.stat().st_mode & 0o777 == 0o700
            assert paths.journal.parent != stage
            assert paths.journal not in stage.rglob("*")
            apt_seen.append("offline" if "--no-download" in argv else "online")
        return subprocess.CompletedProcess(argv, 0, "", "")

    previous = os.umask(mask)
    try:
        with installation.prepare_packages(
            root,
            target=target,
            happ=happ,
            proxy=PROXY,
            run=execute,
            paths=paths,
            release=RELEASE,
            architecture="amd64",
        ) as prepared:
            prepared.install()
            prepared.activate_happ()
    finally:
        os.umask(previous)
    assert apt_seen == ["online", "online", "online", "offline"]
    assert not list(paths.runtime.iterdir())
    assert happ.read_bytes() == original


@pytest.mark.parametrize(
    ("condition", "allowed"),
    [
        ("absent", True),
        ("same", True),
        ("older", False),
        ("newer", False),
        ("partial", False),
        ("reinst-required", False),
        ("empty", False),
        ("absent-with-output", False),
        ("query-failure", False),
        ("missing-query", False),
        ("query-timeout", False),
    ],
)
def test_existing_happ_requires_absence_or_exact_configured_release_before_apt(
    package_fixture: PackageFixture,
    condition: str,
    allowed: bool,
) -> None:
    root, target, happ, paths = package_fixture
    marker = "fixture-private-dpkg-diagnostic"
    queries: list[list[str]] = []
    apt_calls: list[list[str]] = []
    actions: list[list[str]] = []

    def execute(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        if argv[0] == "/usr/bin/dpkg-query":
            queries.append(argv)
            assert argv == [
                "/usr/bin/dpkg-query",
                "--no-pager",
                "--show",
                r"--showformat=${Status}\n${Version}\n",
                "happ",
            ]
            assert options["check"] is False and options["timeout"] == 15
            environment = options["env"]
            assert isinstance(environment, dict) and set(environment) == {"PATH", "LANG", "LC_ALL"}
            if condition == "missing-query":
                raise FileNotFoundError(marker)
            if condition == "query-timeout":
                raise subprocess.TimeoutExpired(argv, 15, stderr=marker)
            code, text = {
                "absent": (1, ""),
                "same": (0, "install ok installed\n4.5.4-333\n"),
                "older": (0, "install ok installed\n4.5.3-1\n"),
                "newer": (0, "install ok installed\n4.5.5-1\n"),
                "partial": (0, "install ok unpacked\n4.5.4-333\n"),
                "reinst-required": (0, "install reinstreq installed\n4.5.4-333\n"),
                "empty": (0, ""),
                "absent-with-output": (1, marker),
                "query-failure": (2, marker),
            }[condition]
            return subprocess.CompletedProcess(argv, code, text, marker)
        if argv[0] == "/usr/bin/systemctl":
            if "--version" in argv:
                return subprocess.CompletedProcess(argv, 0, "systemd 257 (257.9)\n", "")
            if "--property=Version" in argv:
                return subprocess.CompletedProcess(argv, 0, "257.9\n", "")
            actions.append(argv)
        if argv[0] == "/usr/bin/dpkg-deb":
            return metadata(argv)
        if "/usr/bin/apt-get" in argv:
            apt_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    if allowed:
        for _ in range(2):
            with installation.prepare_packages(
                root,
                target=target,
                happ=happ,
                proxy=None,
                run=execute,
                paths=paths,
                release=RELEASE,
                architecture="amd64",
            ) as prepared:
                prepared.install()
                prepared.activate_happ()
        assert len(queries) == 4 and len(apt_calls) == 8
        assert all("restart" not in action for action in actions)
        assert json.loads(paths.journal.read_text())["status"] == "system-complete"
    else:
        with pytest.raises(ValueError, match="manual review") as error:
            with installation.prepare_packages(
                root,
                target=target,
                happ=happ,
                proxy=None,
                run=execute,
                paths=paths,
                release=RELEASE,
                architecture="amd64",
            ):
                pytest.fail("Unsafe installed Happ state reached package preparation")
        assert marker not in str(error.value)
        assert len(queries) == 1 and apt_calls == [] and actions == []
        assert not paths.journal.exists() and not paths.backports.exists()
    assert not list(paths.runtime.iterdir())


@pytest.mark.parametrize("initial", ["absent", "same"])
@pytest.mark.parametrize("changed", ["older", "newer", "partial", "query-failure"])
def test_happ_state_changes_during_downloads_block_offline_apply_and_helper_activation(
    package_fixture: PackageFixture,
    initial: str,
    changed: str,
) -> None:
    root, target, happ, paths = package_fixture
    queries = 0
    apt_calls: list[list[str]] = []
    actions: list[list[str]] = []

    def execute(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        nonlocal queries
        if argv[0] == "/usr/bin/dpkg-query":
            queries += 1
            condition = initial if queries == 1 else changed
            code, text = {
                "absent": (1, ""),
                "same": (0, "install ok installed\n4.5.4-333\n"),
                "older": (0, "install ok installed\n4.5.3-1\n"),
                "newer": (0, "install ok installed\n4.5.5-1\n"),
                "partial": (0, "install ok half-configured\n4.5.4-333\n"),
                "query-failure": (2, ""),
            }[condition]
            return subprocess.CompletedProcess(argv, code, text, "")
        if argv[0] == "/usr/bin/systemctl":
            if "--version" in argv:
                return subprocess.CompletedProcess(argv, 0, "systemd 257 (257.9)\n", "")
            if "--property=Version" in argv:
                return subprocess.CompletedProcess(argv, 0, "257.9\n", "")
            actions.append(argv)
        if argv[0] == "/usr/bin/dpkg-deb":
            return metadata(argv)
        if "/usr/bin/apt-get" in argv:
            apt_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    with pytest.raises(ValueError, match="manual review"):
        with installation.prepare_packages(
            root,
            target=target,
            happ=happ,
            proxy=None,
            run=execute,
            paths=paths,
            release=RELEASE,
            architecture="amd64",
        ) as prepared:
            assert len(apt_calls) == 3 and "--download-only" in apt_calls[-1]
            prepared.install()
            prepared.activate_happ()
            pytest.fail("Changed Happ state reached offline package apply")
    assert queries == 2 and len(apt_calls) == 3
    assert not any("--no-download" in call for call in apt_calls)
    assert actions == [] and not paths.policy.exists() and not paths.backports.exists()
    state = json.loads(paths.journal.read_text())
    assert state["status"] == "failed" and state["stage"] == "packages"


@pytest.mark.parametrize(
    "packages",
    [("curl",), ("--allow-remove-essential",), ("systemd-timesyncd", "systemd-timesyncd")],
)
def test_extra_system_packages_are_a_finite_validated_integration_choice(
    package_fixture,
    packages,
) -> None:
    root, target, happ, paths = package_fixture
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid extra packages reached an executor")

    with pytest.raises(ValueError, match="Unexpected desktop integration package"):
        with installation.prepare_packages(
            root,
            target=target,
            happ=happ,
            proxy=None,
            run=forbidden,
            paths=paths,
            extra_packages=packages,
        ):
            pytest.fail("Invalid extra packages reached apply")
    assert {path: path.read_bytes() for path in before} == before
