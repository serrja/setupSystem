"""Exercise Debian trixie's canonical keyring and pre-journal failures."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from setup_system.adapters.debian import apt_paths, apt_transaction
from setup_system.composition import build_service
from setup_system.domain import identity as account_identity
from setup_system.domain.operations import InstallRequest, Operation

ROOT = Path(__file__).resolve().parents[1]
RELEASE = {"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"}
CANONICAL = "debian-archive-keyring.pgp"
COMPATIBILITY = "debian-archive-keyring.gpg"


def keyrings(tmp_path: Path, kind: str = "regular") -> Path:
    directory = tmp_path / "keyrings"
    directory.mkdir()
    canonical = directory / CANONICAL
    if kind == "regular":
        canonical.write_bytes(b"fixture Debian archive certificates")
        canonical.chmod(0o644)
    elif kind == "symlink":
        outside = tmp_path / "untrusted-keyring.pgp"
        outside.write_bytes(b"outside trust anchor")
        canonical.symlink_to(outside)
    (directory / COMPATIBILITY).symlink_to(CANONICAL)
    return directory


def system_paths(tmp_path: Path, directory: Path) -> apt_paths.SystemPaths:
    runtime = tmp_path / "keyring-run"
    runtime.mkdir(mode=0o700)
    return apt_paths.SystemPaths(
        tmp_path / "etc/setup-system-backports.sources",
        tmp_path / "state/install.json",
        directory / apt_paths.SystemPaths().keyring.name,
        runtime,
        tmp_path / "policy-rc.d",
        happ_unit=tmp_path / "etc-systemd/happd.service",
        happ_runtime_unit=tmp_path / "run-systemd/happd.service",
    )


def test_trixie_canonical_keyring_completes_and_repeats_system_stage(
    tmp_path: Path,
    package_fixture: tuple[Path, account_identity.TargetUser, Path, apt_paths.SystemPaths],
) -> None:
    root, _target, _happ, _ = package_fixture
    directory = keyrings(tmp_path)
    paths = system_paths(tmp_path, directory)
    calls: list[list[str]] = []
    sources: list[bytes] = []

    def execute(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
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
            return subprocess.CompletedProcess(
                argv, 0, "Package: happ\nVersion: 4.5.4-333\nArchitecture: amd64\n", ""
            )
        if "/usr/bin/apt-get" not in argv:
            return subprocess.CompletedProcess(argv, 0, "", "")
        calls.append(argv)
        source = next(
            arg.split("=", 1)[1] for arg in argv if arg.startswith("Dir::Etc::sourcelist=")
        )
        sources.append(Path(source).read_bytes())
        return subprocess.CompletedProcess(
            argv, 0, "Inst hyprland (0.55.2+ds-1~bpo13+1 Debian)", ""
        )

    for _ in range(2):
        with apt_transaction.prepare_packages(
            root,
            run=execute,
            paths=paths,
            release=RELEASE,
            architecture="amd64",
        ) as prepared:
            prepared.install()

        state = json.loads(paths.journal.read_text())
        assert state["status"] == "packages-complete" and state["stage"] == "packages"
    assert paths.keyring.name == CANONICAL
    expected = f"Signed-By: {directory / CANONICAL}".encode()
    assert len(calls) == 8 and all(expected in source for source in sources)
    assert expected in paths.backports.read_bytes()
    assert paths.backports.read_bytes().count(b"trixie-backports") == 1
    assert (directory / COMPATIBILITY).is_symlink()
    assert (directory / COMPATIBILITY).readlink() == Path(CANONICAL)
    assert (directory / CANONICAL).read_bytes() == b"fixture Debian archive certificates"
    assert not paths.policy.exists()


@pytest.mark.parametrize("kind", ["missing", "symlink"])
def test_untrusted_or_missing_canonical_keyring_stops_before_apt_and_journal(
    tmp_path: Path,
    kind: str,
    package_fixture: tuple[Path, account_identity.TargetUser, Path, apt_paths.SystemPaths],
) -> None:
    root, _target, _happ, _ = package_fixture
    directory = keyrings(tmp_path, kind)
    paths = system_paths(tmp_path, directory)
    with pytest.raises(ValueError, match="keyring"):
        with apt_transaction.prepare_packages(
            root,
            run=lambda *args, **kwargs: pytest.fail("APT reached an invalid trust anchor"),
            paths=paths,
            release=RELEASE,
            architecture="amd64",
        ):
            pytest.fail("Invalid trust anchor entered package preparation")
    assert not paths.journal.exists() and not paths.backports.exists()
    assert (directory / COMPATIBILITY).is_symlink()


def test_prejournal_failure_exposes_reason_without_claiming_a_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from setup_system.domain.settings import Section, UserSettings

    monkeypatch.setattr(
        "setup_system.application.workstation_plan.platform_blocker", lambda root: ()
    )

    service = build_service(ROOT)
    monkeypatch.setattr(
        service,
        "_apply",
        lambda *args: (_ for _ in ()).throw(
            ValueError("canonical Debian archive keyring is missing")
        ),
    )
    assert (
        service.run(
            InstallRequest(
                operation=Operation.APPLY,
                section=Section.WORKSPACE,
                settings=UserSettings(apps=("codex",)),
                yes=True,
            )
        )
        == 5
    )
    error = capsys.readouterr().err
    assert "keyring is missing" in error
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("kind", ["validation", "apt"])
def test_failure_diagnostics_redact_dynamic_credentials_and_preserve_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: str,
) -> None:
    from setup_system.domain.settings import Section, UserSettings

    monkeypatch.setattr(
        "setup_system.application.workstation_plan.platform_blocker", lambda root: ()
    )

    marker = hashlib.sha256(str(tmp_path).encode()).hexdigest()
    diagnostic = "archive signature validation failed https://" + marker + ":" + marker
    diagnostic += "@example.invalid/archive?token=" + marker + " password=" + marker
    service = build_service(ROOT)

    def fail(*args):
        raise ValueError(diagnostic)

    monkeypatch.setattr(service, "_apply", fail)
    assert (
        service.run(
            InstallRequest(
                operation=Operation.APPLY,
                section=Section.WORKSPACE,
                settings=UserSettings(apps=("codex",)),
                yes=True,
            )
        )
        == 5
    )
    error = capsys.readouterr().err
    assert "archive signature validation failed" in error and marker not in error
    assert not list(tmp_path.iterdir())
