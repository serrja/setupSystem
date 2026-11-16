"""Exercise one Bash entrypoint call without installing packages or becoming root."""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Invocation:
    process: subprocess.CompletedProcess[str]
    calls: tuple[str, ...]
    read_checks: tuple[str, ...]
    home: Path


def invoke(
    tmp_path: Path,
    *arguments: str,
    python: bool = False,
    invalid_identity: str | None = None,
    unprivileged: bool = False,
) -> Invocation:
    """Use actual owned fixture paths and fake only external identity/execution boundaries."""
    home = tmp_path / "home with spaces"
    home.mkdir()
    elsewhere = tmp_path / "unrelated cwd"
    elsewhere.mkdir()
    log = tmp_path / "calls"
    read_log = tmp_path / "read checks"
    uid = os.getuid()
    gid = os.getgid()
    account_uid = 0 if invalid_identity == "zero" else uid
    shell = "/usr/sbin/nologin" if invalid_identity == "nologin" else "/bin/bash"
    if invalid_identity == "xdg-symlink":
        outside = tmp_path / "outside"
        outside.mkdir()
        (home / ".config").symlink_to(outside, target_is_directory=True)
    sudo_user = "missing" if invalid_identity == "unknown" else "alice"
    sudo_uid = uid + 1 if invalid_identity == "uid" else account_uid
    sudo_gid = gid + 1 if invalid_identity == "gid" else gid
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/root",
        "LANG": "C.UTF-8",
        "SUDO_USER": sudo_user,
        "SUDO_UID": str(sudo_uid),
        "SUDO_GID": str(sudo_gid),
        # It must not become a production environment hook or a prerequisite.
        "APT_CONFIG": str(tmp_path / "untrusted apt config"),
    }
    if invalid_identity == "missing" or unprivileged:
        environment = {
            key: value for key, value in environment.items() if not key.startswith("SUDO_")
        }
    script = f"""
source {shlex.quote(str(ROOT / "setup.sh"))}
fixture_home={shlex.quote(str(home))}
fixture_log={shlex.quote(str(log))}
fixture_read_log={shlex.quote(str(read_log))}
setup_release_value() {{
    case "$1" in
        ID) printf debian;; VERSION_ID) printf 13;;
        VERSION_CODENAME) printf trixie;; *) return 2;;
    esac
}}
dpkg() {{ [[ $1 == --print-architecture ]] && printf amd64; }}
id() {{
    case "$*" in
        '-u') printf %s {uid if unprivileged else 0};;
        '-u alice') printf %s {account_uid};;
        '-g alice') printf %s {gid};;
        '-un') printf alice;;
        *) return 2;;
    esac
}}
getent() {{
    case "$1:$2" in
        passwd:alice)
            printf 'alice:x:%s:%s::%s:%s\\n' \\
                {account_uid} {gid} "$fixture_home" {shlex.quote(shell)};;
        group:{gid}) printf 'fixture:x:%s:\\n' {gid};;
        *) return 2;;
    esac
}}
setpriv() {{
    printf '%s\\0' "$@" >> "$fixture_read_log"
    [[ $# == 8 && $1 == --reuid && $2 == {account_uid} &&
        $3 == --regid && $4 == {gid} && $5 == --init-groups &&
        $6 == /usr/bin/test && ( $7 == -x || $7 == -r ) ]] || return 2
    [[ {shlex.quote(invalid_identity or "")} != source-denied ]] || return 1
    if [[ {shlex.quote(invalid_identity or "")} == source-file-denied &&
        $7 == -r && $8 == */bootstrap.py ]]; then return 1; fi
    builtin test "$7" "$8"
}}
env() {{
    printf '%s\\0' env "$@" >> "$fixture_read_log"
    [[ $1 == -i && $2 == PATH=/usr/sbin:/usr/bin:/sbin:/bin && $3 == LANG=C &&
        $4 == setpriv ]] || return 2
    shift 3
    "$@"
}}
command() {{
    if [[ $1 == -v && $2 == python3 ]]; then return {0 if python else 1}; fi
    if [[ $1 == -v && $2 == uv ]]; then return 1; fi
    if [[ $1 == -v && $2 == setpriv &&
        {shlex.quote(invalid_identity or "")} == setpriv-missing ]]; then return 1; fi
    builtin command "$@"
}}
setup_python_prerequisite() {{ printf 'prerequisite\\0' >> "$fixture_log"; }}
exec() {{ printf '%s\\0' exec "$@" >> "$fixture_log"; exit 0; }}
setup_main "$@"
"""
    process = subprocess.run(
        ["/usr/bin/bash", "-c", script, "setup-fixture", *arguments],
        cwd=elsewhere,
        env=environment,
        input="",
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    calls = tuple(log.read_text().split("\0")[:-1]) if log.exists() else ()
    read_checks = tuple(read_log.read_text().split("\0")[:-1]) if read_log.exists() else ()
    return Invocation(process, calls, read_checks, home)


@pytest.mark.parametrize("python", [False, True])
def test_one_apply_prepares_missing_python_and_forwards_verified_account(
    tmp_path: Path, python: bool
) -> None:
    config = tmp_path / "home with spaces/config with spaces"
    result = invoke(tmp_path, "--yes", "--xdg-config-home", str(config), python=python)
    assert result.process.returncode == 0, result.process.stderr
    assert result.calls.count("prerequisite") == (0 if python else 1)
    assert result.read_checks and str(ROOT / "bootstrap.py") in result.read_checks
    assert result.read_checks[:5] == (
        "env",
        "-i",
        "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG=C",
        "setpriv",
    )
    assert not any(
        value.startswith(("APT_CONFIG=", "SUDO_", "GH_TOKEN=")) for value in result.read_checks
    )
    executable = result.calls.index("exec")
    forwarded = result.calls[executable + 1 :]
    assert forwarded[:2] == ("env", "-i")
    assert "HOME=" + str(result.home) in forwarded
    assert str(ROOT / "bootstrap.py") in forwarded
    assert forwarded[forwarded.index(str(ROOT / "bootstrap.py")) + 1] == "apply"
    assert forwarded[forwarded.index("--user") + 1] == "alice"
    assert forwarded[forwarded.index("--xdg-config-home") + 1] == str(config)
    assert forwarded[-1] == "--yes"
    assert not any(value.startswith(("APT_CONFIG=", "SUDO_", "GH_TOKEN=")) for value in forwarded)
    assert list(result.home.iterdir()) == []


@pytest.mark.parametrize("python", [False, True])
@pytest.mark.parametrize("mode", ["plan", "--dry-run", "--help"])
def test_read_only_modes_do_not_prepare_dependencies_or_touch_home(
    tmp_path: Path, python: bool, mode: str
) -> None:
    result = invoke(tmp_path, mode, python=python, unprivileged=True)
    assert result.process.returncode == 0, result.process.stderr
    assert "prerequisite" not in result.calls
    if mode == "--help":
        assert not result.calls
    elif python:
        assert result.calls[result.calls.index(str(ROOT / "bootstrap.py")) + 1] == "plan"
        assert "--yes" not in result.calls
    else:
        assert not result.calls and "Preliminary plan" in result.process.stdout
    assert list(result.home.iterdir()) == []


@pytest.mark.parametrize("arguments", [("--help", "apply", "--yes"), ("apply", "--yes", "--help")])
def test_help_stays_read_only_in_both_argument_orders(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    result = invoke(tmp_path, *arguments)
    assert result.process.returncode == 0 and not result.calls
    assert "Usage:" in result.process.stdout and list(result.home.iterdir()) == []


def test_no_python_apply_without_tty_or_explicit_consent_stops(tmp_path: Path) -> None:
    result = invoke(tmp_path)
    assert result.process.returncode == 2
    assert "No TTY" in result.process.stderr and not result.calls
    assert list(result.home.iterdir()) == []


@pytest.mark.parametrize(
    "arguments",
    [
        ("install",),
        ("desktop",),
        ("--profile", "terminalDev"),
        ("--install-debian-packages",),
        ("--unknown",),
    ],
)
def test_unknown_and_retired_arguments_cannot_reach_default_apply(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    result = invoke(tmp_path, *arguments)
    assert result.process.returncode == 2
    assert "Unknown or retired argument" in result.process.stderr and not result.calls
    assert list(result.home.iterdir()) == []


@pytest.mark.parametrize(
    "case", ["xdg-symlink", "nologin", "uid", "gid", "missing", "unknown", "zero"]
)
def test_invalid_identity_or_default_xdg_path_fails_before_prerequisites(
    tmp_path: Path, case: str
) -> None:
    result = invoke(tmp_path, "--yes", invalid_identity=case)
    assert result.process.returncode == 2, result.process.stdout + result.process.stderr
    assert not result.calls
    assert not (result.home / ".local").exists()


@pytest.mark.parametrize("python", [False, True])
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("source-denied", "not accessible to the target user"),
        ("source-file-denied", "not readable by the target user: bootstrap.py"),
        ("setpriv-missing", "requires Debian util-linux setpriv"),
    ],
)
def test_source_denied_to_target_user_fails_before_any_apply(
    tmp_path: Path, python: bool, case: str, message: str
) -> None:
    result = invoke(tmp_path, "--yes", python=python, invalid_identity=case)
    assert result.process.returncode == 2
    assert message in result.process.stderr
    assert bool(result.read_checks) == (case != "setpriv-missing")
    assert not result.calls
    assert list(result.home.iterdir()) == []
