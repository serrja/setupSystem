"""Exercise Debian trixie's canonical keyring and pre-journal failures."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from setup_system import installation as install
from setup_system.application.installer import InstallationService, InstallRequest, Operation

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


def system_paths(tmp_path: Path, directory: Path) -> install.SystemPaths:
    runtime = tmp_path / "run"
    runtime.mkdir()
    return install.SystemPaths(
        tmp_path / "etc/setup-system-backports.sources",
        tmp_path / "state/install.json",
        directory / install.SystemPaths().keyring.name,
        runtime,
        tmp_path / "policy-rc.d",
    )


def test_trixie_canonical_keyring_completes_and_repeats_system_stage(tmp_path: Path) -> None:
    directory = keyrings(tmp_path)
    paths = system_paths(tmp_path, directory)
    calls: list[list[str]] = []
    sources: list[bytes] = []

    def execute(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        source = next(
            arg.split("=", 1)[1] for arg in argv if arg.startswith("Dir::Etc::sourcelist=")
        )
        sources.append(Path(source).read_bytes())
        return subprocess.CompletedProcess(argv, 0, "Inst hyprland (0.55.2 Debian)", "")

    for _ in range(2):
        install.install_system(
            ROOT, run=execute, paths=paths, release=RELEASE, architecture="amd64"
        )
        state = json.loads(paths.journal.read_text())
        assert state["status"] == "system-complete" and state["stage"] == "user-runtime"
    assert paths.keyring.name == CANONICAL
    expected = f"Signed-By: {directory / CANONICAL}".encode()
    assert len(calls) == 6 and all(expected in source for source in sources)
    assert expected in paths.backports.read_bytes()
    assert paths.backports.read_bytes().count(b"trixie-backports") == 1
    assert (directory / COMPATIBILITY).is_symlink()
    assert (directory / COMPATIBILITY).readlink() == Path(CANONICAL)
    assert (directory / CANONICAL).read_bytes() == b"fixture Debian archive certificates"
    assert not paths.policy.exists()


@pytest.mark.parametrize("kind", ["missing", "symlink"])
def test_untrusted_or_missing_canonical_keyring_stops_before_apt_and_journal(
    tmp_path: Path, kind: str
) -> None:
    directory = keyrings(tmp_path, kind)
    paths = system_paths(tmp_path, directory)
    with pytest.raises(ValueError, match="keyring"):
        install.install_system(
            ROOT,
            run=lambda *args, **kwargs: pytest.fail("APT reached an invalid trust anchor"),
            paths=paths,
            release=RELEASE,
            architecture="amd64",
        )
    assert not paths.journal.exists() and not paths.backports.exists()
    assert (directory / COMPATIBILITY).is_symlink()


def prerequisite(
    tmp_path: Path, directory: Path
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
    source = (ROOT / "setup.sh").read_text()
    start = source.index("setup_python_prerequisite() (")
    function = source[start : source.index("\nsetup_handoff()", start)]
    staging = tmp_path / "prerequisite-run"
    staging.mkdir()
    log = tmp_path / "external-calls.jsonl"
    timeout = tmp_path / "fixture-timeout"
    timeout.write_text(
        "#!" + sys.executable + "\n"
        "import json, pathlib, sys\n"
        f"log = pathlib.Path({str(log)!r})\n"
        "args = sys.argv[1:]\n"
        "source = next(a.split('=', 1)[1] for a in args if a.startswith('Dir::Etc::sourcelist='))\n"
        "record = {'args': args, 'source': pathlib.Path(source).read_text()}\n"
        "with log.open('a') as stream: stream.write(json.dumps(record) + '\\n')\n"
    )
    timeout.chmod(0o700)
    # Remap fixed host paths at the test boundary, then execute the production body.
    function = function.replace("/usr/share/keyrings/", str(directory) + "/")
    function = function.replace(
        "/run/setup-system-prerequisites.XXXXXXXX",
        shlex.quote(str(staging / "setup-system-prerequisites.XXXXXXXX")),
    )
    function = function.replace("/usr/bin/timeout", shlex.quote(str(timeout)))
    script = (
        f"source {shlex.quote(str(ROOT / 'setup.sh'))}\n"
        f"setup_root={shlex.quote(str(ROOT))}\n" + function + "\nsetup_python_prerequisite\n"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    records = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    assert not list(staging.iterdir()), "Prerequisite staging leaked after return"
    return result, records


def test_no_python_shell_uses_canonical_trixie_keyring_without_following_alias(
    tmp_path: Path,
) -> None:
    directory = keyrings(tmp_path)
    result, calls = prerequisite(tmp_path, directory)
    assert result.returncode == 0, result.stderr
    assert len(calls) == 3
    assert all(f"Signed-By: {directory / CANONICAL}" in call["source"] for call in calls)
    arguments = [call["args"] for call in calls]
    assert "update" in arguments[0] and "--simulate" in arguments[1] and "--yes" in arguments[2]
    assert all("Acquire::AllowInsecureRepositories=false" in args for args in arguments)
    assert all("APT::Get::AllowUnauthenticated=false" in args for args in arguments)
    assert all("--no-remove" in args for args in arguments[1:])
    assert (directory / COMPATIBILITY).readlink() == Path(CANONICAL)
    assert (directory / CANONICAL).read_bytes() == b"fixture Debian archive certificates"


@pytest.mark.parametrize("kind", ["missing", "symlink"])
def test_no_python_shell_rejects_missing_or_symlink_canonical_keyring(
    tmp_path: Path, kind: str
) -> None:
    directory = keyrings(tmp_path, kind)
    result, calls = prerequisite(tmp_path, directory)
    assert result.returncode != 0 and "keyring" in result.stderr
    assert not calls and (directory / COMPATIBILITY).is_symlink()


def test_prejournal_failure_exposes_reason_without_claiming_a_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "target home"
    home.mkdir()
    target = install.TargetUser("alice", os.getuid(), os.getgid(), home, "/bin/bash")
    paths = install.SystemPaths(journal=tmp_path / "absent-state/install.json")
    monkeypatch.setattr(install, "SystemPaths", lambda: paths)
    monkeypatch.setattr(install, "current_packages", lambda root: ["hyprland=fixture"])
    monkeypatch.setattr(install, "resolve_target", lambda name: target)
    monkeypatch.setattr(install, "validate_source_access", lambda *a, **kw: None)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    reason = "canonical Debian archive keyring is missing"

    def fail(*args: object, **kwargs: object) -> None:
        raise ValueError(reason)

    monkeypatch.setattr(install, "install_system", fail)
    monkeypatch.setattr(install, "run_command", lambda *a, **kw: pytest.fail("User stage reached"))
    assert (
        InstallationService(ROOT).run(InstallRequest(Operation.APPLY, user="alice", yes=True)) == 5
    )
    error = capsys.readouterr().err
    assert reason in error
    assert "journal" in error.lower() and "not created" in error.lower()
    assert f"See {paths.journal}" not in error
    assert not paths.journal.exists() and not list(home.iterdir())


@pytest.mark.parametrize("kind", ["validation", "apt"])
def test_failure_diagnostics_redact_dynamic_credentials_and_preserve_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: str,
) -> None:
    home = tmp_path / "target home"
    home.mkdir()
    target = install.TargetUser("alice", os.getuid(), os.getgid(), home, "/bin/bash")
    paths = install.SystemPaths(journal=tmp_path / "absent-state/install.json")
    monkeypatch.setattr(install, "SystemPaths", lambda: paths)
    monkeypatch.setattr(install, "current_packages", lambda root: ["hyprland=fixture"])
    monkeypatch.setattr(install, "resolve_target", lambda name: target)
    monkeypatch.setattr(install, "validate_source_access", lambda *a, **kw: None)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    marker = hashlib.sha256(str(tmp_path).encode()).hexdigest()
    reason = "archive signature validation failed"
    diagnostic = (
        reason
        + " https://"
        + marker
        + ":"
        + marker
        + "@example.invalid/archive?token="
        + marker
        + " password="
        + marker
        + " Authorization: Bearer "
        + marker
        + " Proxy-Authorization: Basic "
        + marker
        + "\nAuthorization: Digest nonce="
        + marker
        + ",response="
        + marker
        + "\nAuthorization: Custom "
        + marker
        + "\x1b[31m\n"
        + "x" * 4000
    )

    def fail(*args: object, **kwargs: object) -> None:
        if kind == "apt":
            raise subprocess.CalledProcessError(100, ["apt-get", marker], stderr=diagnostic)
        raise ValueError(diagnostic)

    monkeypatch.setattr(install, "install_system", fail)
    monkeypatch.setattr(install, "run_command", lambda *a, **kw: pytest.fail("User stage reached"))
    assert (
        InstallationService(ROOT).run(InstallRequest(Operation.APPLY, user="alice", yes=True)) == 5
    )
    error = capsys.readouterr().err
    assert reason in error and marker not in error
    assert "\x1b" not in error and len(error) < 3000
    assert not paths.journal.exists() and not list(home.iterdir())
