"""Exercise one Bash entrypoint call without installing packages or becoming root."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
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
    subscription: Path | None


def invoke(
    tmp_path: Path,
    *arguments: str,
    python: bool = False,
    ca: bool = True,
    prerequisite_failure: bool = False,
    invalid_identity: str | None = None,
    unprivileged: bool = False,
    tty: bool = False,
    init_case: str = "supported",
    vpn_input: str | None = None,
) -> Invocation:
    """Use actual owned fixture paths and fake only external identity/execution boundaries."""
    home = tmp_path / "home with spaces"
    home.mkdir()
    private: Path | None = None
    if vpn_input is not None:
        private = home / "vpn input with spaces"
        private.write_text("https://provider.invalid/subscription?token=SYNTHETIC_PRIVATE_MARKER\n")
        private.chmod(0o600)
        if vpn_input == "mode":
            private.chmod(0o640)
        elif vpn_input == "empty":
            private.write_bytes(b"")
        elif vpn_input == "oversize":
            private.write_bytes(b"x" * 8193)
        elif vpn_input == "max-size":
            prefix = b"https://provider.invalid/subscription?token="
            private.write_bytes(prefix + b"x" * (8192 - len(prefix)))
        elif vpn_input == "control":
            actual = home / "input\x01filename"
            private.rename(actual)
            private = actual
        elif vpn_input == "symlink":
            actual = home / "owned subscription"
            private.rename(actual)
            private.symlink_to(actual)
        elif vpn_input == "hardlink":
            os.link(private, home / "second link")
        elif vpn_input == "directory":
            private.unlink()
            private.mkdir(mode=0o700)
        elif vpn_input == "outside":
            actual = tmp_path / "outside private file"
            private.rename(actual)
            private = actual
        elif vpn_input == "relative":
            private = Path("relative subscription")
        elif vpn_input == "traversal":
            (home / "nested").mkdir()
            private = home / "nested/../vpn input with spaces"
        elif vpn_input == "parent-symlink":
            folder = home / "linked"
            folder.symlink_to(home, target_is_directory=True)
            private = folder / "vpn input with spaces"
        arguments = (*arguments, "--vpn-config-file", str(private))
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
    client_version = (
        "systemd 258" if init_case == "client-version" else "systemd 257 (257.9-1~deb13u1)"
    )
    manager_version = "256.1" if init_case == "manager-version" else "257.9-1~deb13u1"
    manager_arguments = "--system --no-pager --no-ask-password show --property=Version --value"
    script = f"""
source {shlex.quote(str(ROOT / "setup.sh"))}
fixture_home={shlex.quote(str(home))}
fixture_vpn={shlex.quote(str(private) if private is not None else "")}
fixture_log={shlex.quote(str(log))}
fixture_read_log={shlex.quote(str(read_log))}
setup_has_tty() {{ return {0 if tty else 1}; }}
setup_systemctl() {{
    if [[ $1 == --version ]]; then
        [[ {shlex.quote(init_case)} != client-missing ]] || return 127
        printf '%s\\n' {shlex.quote(client_version)}
    else
        [[ "$*" == {shlex.quote(manager_arguments)} ]] || return 2
        [[ {shlex.quote(init_case)} != manager-missing ]] || return 1
        printf '%s\\n' {shlex.quote(manager_version)}
    fi
}}
stat() {{
    local last
    for last in "$@"; do :; done
    if [[ {shlex.quote(vpn_input or "")} == owner && $2 == %u && $last == "$fixture_vpn" ]]; then
        printf '%s' {uid + 1}
    else
        /usr/bin/stat "$@"
    fi
}}
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
        $7 == -r && $8 == */src/setup_system/_installer_entry.py ]]; then return 1; fi
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
fixture_ca_ready={1 if ca else 0}
setup_ca_ready() {{ [[ $fixture_ca_ready == 1 ]]; }}
setup_python_prerequisite() {{
    printf 'prerequisite\\0' >> "$fixture_log"
    [[ {1 if prerequisite_failure else 0} == 0 ]] || return 5
    fixture_ca_ready=1
}}
read() {{
    if [[ "$*" == *'Apply the complete plan?'* ]]; then
        printf 'consent\\0' >> "$fixture_log"
    fi
    builtin read "$@"
}}
exec() {{ printf '%s\\0' exec "$@" >> "$fixture_log"; exit 0; }}
setup_main "$@"
"""
    process = subprocess.run(
        ["/usr/bin/bash", "-c", script, "setup-fixture", *arguments],
        cwd=elsewhere,
        env=environment,
        input="yes\n" if tty else "",
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    calls = tuple(log.read_text().split("\0")[:-1]) if log.exists() else ()
    read_checks = tuple(read_log.read_text().split("\0")[:-1]) if read_log.exists() else ()
    return Invocation(process, calls, read_checks, home, private)


@pytest.mark.parametrize("python", [False, True])
def test_one_apply_prepares_missing_python_and_forwards_verified_account(
    tmp_path: Path, python: bool
) -> None:
    config = tmp_path / "home with spaces/config with spaces"
    result = invoke(tmp_path, "--yes", "--xdg-config-home", str(config), python=python, tty=True)
    assert result.process.returncode == 0, result.process.stderr
    assert result.calls.count("prerequisite") == (0 if python else 1)
    assert (
        result.read_checks
        and str(ROOT / "src/setup_system/_installer_entry.py") in result.read_checks
    )
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
    assert str(ROOT / "src/setup_system/_installer_entry.py") in forwarded
    assert (
        forwarded[forwarded.index(str(ROOT / "src/setup_system/_installer_entry.py")) + 1]
        == "apply"
    )
    handoff = forwarded[forwarded.index(str(ROOT / "src/setup_system/_installer_entry.py")) + 1 :]
    assert handoff == (
        "apply",
        "alice",
        str(config),
        str(result.home / ".local/share"),
        str(result.home / ".local/state"),
        str(result.home / ".cache"),
        "1",
        "-",
    )
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
        assert (
            result.calls[result.calls.index(str(ROOT / "src/setup_system/_installer_entry.py")) + 1]
            == "plan"
        )
        assert result.calls[-2:] == ("0", "-")
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
        (
            "source-file-denied",
            "not readable by the target user: src/setup_system/_installer_entry.py",
        ),
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


@pytest.mark.parametrize("python", [False, True])
def test_unattended_apply_requires_private_file_before_python_or_apply(
    tmp_path: Path, python: bool
) -> None:
    result = invoke(tmp_path, "--yes", python=python)
    assert result.process.returncode == 2
    assert "private --vpn-config-file" in result.process.stderr
    assert not result.calls and list(result.home.iterdir()) == []


@pytest.mark.parametrize("python", [False, True])
def test_unattended_private_file_is_preserved_and_only_its_path_is_forwarded(
    tmp_path: Path, python: bool
) -> None:
    result = invoke(tmp_path, "--yes", python=python, vpn_input="valid")
    assert result.process.returncode == 0, result.process.stderr
    assert result.subscription is not None
    assert result.calls.count("prerequisite") == (not python)
    offset = result.calls.index(str(ROOT / "src/setup_system/_installer_entry.py")) + 1
    handoff = result.calls[offset:]
    assert len(handoff) == 8 and handoff[-2:] == ("1", str(result.subscription))
    assert result.subscription.read_text().startswith("https://provider.invalid/")
    assert result.subscription.stat().st_mode & 0o777 == 0o600
    assert result.subscription.stat().st_nlink == 1
    combined = result.process.stdout + result.process.stderr + "\n".join(result.calls)
    assert "SYNTHETIC_PRIVATE_MARKER" not in combined and "https://provider.invalid" not in combined
    assert not (result.home / ".local").exists()


@pytest.mark.parametrize("python", [False, True])
@pytest.mark.parametrize(
    "case",
    [
        "mode",
        "empty",
        "oversize",
        "symlink",
        "hardlink",
        "directory",
        "outside",
        "relative",
        "traversal",
        "parent-symlink",
        "owner",
        "control",
    ],
)
def test_invalid_private_file_fails_without_mutation_or_secret_output(
    tmp_path: Path, python: bool, case: str
) -> None:
    result = invoke(tmp_path, "--yes", python=python, vpn_input=case)
    assert result.process.returncode == 2
    assert "VPN input" in result.process.stderr
    assert not result.calls and not (result.home / ".local").exists()
    assert "SYNTHETIC_PRIVATE_MARKER" not in result.process.stdout + result.process.stderr


@pytest.mark.parametrize("python", [False, True])
def test_plan_with_private_file_remains_read_only(tmp_path: Path, python: bool) -> None:
    result = invoke(tmp_path, "plan", python=python, unprivileged=True, vpn_input="valid")
    assert result.process.returncode == 0, result.process.stderr
    assert "prerequisite" not in result.calls
    if python:
        assert result.calls[-2:] == ("0", str(result.subscription))
    assert result.subscription is not None and result.subscription.is_file()
    assert not (result.home / ".local").exists()
    assert "SYNTHETIC_PRIVATE_MARKER" not in result.process.stdout + result.process.stderr


@pytest.mark.parametrize(
    "case", ["client-missing", "client-version", "manager-missing", "manager-version"]
)
def test_systemd_requirement_is_read_only_and_fails_before_apply(tmp_path: Path, case: str) -> None:
    result = invoke(tmp_path, "plan", init_case=case, unprivileged=True)
    assert result.process.returncode == 2
    assert "systemd 257" in result.process.stderr
    assert not result.calls and list(result.home.iterdir()) == []


@pytest.mark.parametrize("python", [False, True])
def test_tty_without_arguments_confirms_once_and_forwards_no_subscription_value(
    tmp_path: Path,
    python: bool,
) -> None:
    result = invoke(tmp_path, tty=True, python=python)
    assert result.process.returncode == 0, result.process.stderr
    offset = result.calls.index(str(ROOT / "src/setup_system/_installer_entry.py")) + 1
    assert result.calls[offset:][-2:] == ("0" if python else "1", "-")
    assert result.calls.count("prerequisite") == (not python)
    assert list(result.home.iterdir()) == []


def test_unknown_argument_does_not_echo_private_subscription(tmp_path: Path) -> None:
    marker = "SYNTHETIC_PRIVATE_MARKER"
    result = invoke(tmp_path, "https://provider.invalid/private?token=" + marker)
    assert result.process.returncode == 2 and not result.calls
    assert marker not in result.process.stdout + result.process.stderr
    assert "https://provider.invalid" not in result.process.stdout + result.process.stderr


@pytest.mark.parametrize("python", [False, True])
def test_private_file_accepts_upper_size_boundary(tmp_path: Path, python: bool) -> None:
    result = invoke(tmp_path, "--yes", python=python, vpn_input="max-size")
    assert result.process.returncode == 0, result.process.stderr
    assert result.subscription is not None and result.subscription.stat().st_size == 8192


@pytest.mark.parametrize(
    "arguments",
    [
        ("--vpn-config-file",),
        ("--vpn-config-file", "/one", "--vpn-config-file", "/two"),
    ],
)
def test_private_file_option_requires_one_value(tmp_path: Path, arguments: tuple[str, ...]) -> None:
    result = invoke(tmp_path, *arguments)
    assert result.process.returncode == 2 and not result.calls
    assert list(result.home.iterdir()) == []


def test_subscription_url_cannot_be_used_as_private_filename(tmp_path: Path) -> None:
    marker = "SYNTHETIC_PRIVATE_MARKER"
    result = invoke(
        tmp_path, "--yes", "--vpn-config-file", "https://provider.invalid/?token=" + marker
    )
    assert result.process.returncode == 2 and not result.calls
    assert "VPN input" in result.process.stderr
    assert marker not in result.process.stdout + result.process.stderr


@pytest.mark.parametrize("mask", [0o002, 0o077, 0o777])
def test_signed_bootstrap_files_are_readable_by_native_apt_sandbox(
    tmp_path: Path, mask: int
) -> None:
    stage = tmp_path / "signed metadata"
    stage.mkdir(mode=0o700)
    script = (
        f"source {shlex.quote(str(ROOT / 'setup.sh'))}\n"
        f"setup_root={shlex.quote(str(ROOT))}\n"
        f"umask {mask:03o}\n"
        f"setup_prerequisite_files {shlex.quote(str(stage))}\n"
    )
    result = subprocess.run(
        ["/usr/bin/bash", "-c", script],
        env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for directory in (
        stage,
        stage / "lists",
        stage / "lists/partial",
        stage / "archives",
        stage / "archives/partial",
    ):
        assert directory.stat().st_mode & 0o777 == 0o755
        assert directory.stat().st_uid == os.getuid()
    source = stage / "debian.sources"
    assert source.stat().st_mode & 0o777 == 0o644
    assert "Suites: trixie\n" in source.read_text()
    assert "Signed-By: /usr/share/keyrings/debian-archive-keyring.pgp\n" in source.read_text()


@pytest.mark.parametrize("tty", [False, True])
def test_python_without_ca_prepares_signed_prerequisites_before_handoff(
    tmp_path: Path, tty: bool
) -> None:
    result = invoke(
        tmp_path,
        *(() if tty else ("--yes",)),
        python=True,
        ca=False,
        tty=tty,
        vpn_input=None if tty else "valid",
    )
    assert result.process.returncode == 0, result.process.stderr
    assert result.calls.count("prerequisite") == 1
    assert result.calls.index("prerequisite") < result.calls.index("exec")
    assert result.calls.count("consent") == int(tty)
    assert result.calls[-2] == "1"
    assert "Python/CA bootstrap" in result.process.stdout
    assert not (result.home / ".local").exists()


@pytest.mark.parametrize("mode", ["plan", "--dry-run", "--help"])
@pytest.mark.parametrize("python", [False, True])
def test_missing_ca_read_only_modes_never_bootstrap_or_prompt(
    tmp_path: Path, mode: str, python: bool
) -> None:
    result = invoke(tmp_path, mode, python=python, ca=False, unprivileged=True)
    assert result.process.returncode == 0, result.process.stderr
    assert "prerequisite" not in result.calls and "consent" not in result.calls
    if mode != "--help":
        assert "CA" in result.process.stdout
    assert list(result.home.iterdir()) == []


def test_failed_ca_bootstrap_never_hands_off_to_https_runtime(tmp_path: Path) -> None:
    result = invoke(
        tmp_path, "--yes", python=True, ca=False, prerequisite_failure=True, vpn_input="valid"
    )
    assert result.process.returncode == 5
    assert result.calls == ("prerequisite",)
    assert "FAILED: prerequisites" in result.process.stderr
    assert not (result.home / ".local").exists()


@pytest.mark.parametrize(
    ("package", "bundle_kind", "ready"),
    [
        ("installed", "regular", True),
        ("absent", "regular", False),
        ("config-files", "regular", False),
        ("installed", "missing", False),
        ("installed", "empty", False),
        ("installed", "directory", False),
        ("installed", "symlink", False),
        ("installed", "fifo", False),
    ],
)
def test_ca_readiness_checks_package_and_bundle_without_writing(
    tmp_path: Path, package: str, bundle_kind: str, ready: bool
) -> None:
    bundle = tmp_path / "ca bundle.crt"
    if bundle_kind == "regular":
        bundle.write_bytes(b"fixture trusted certificate")
    elif bundle_kind == "empty":
        bundle.touch()
    elif bundle_kind == "directory":
        bundle.mkdir()
    elif bundle_kind == "fifo":
        os.mkfifo(bundle)
    elif bundle_kind == "symlink":
        actual = tmp_path / "external bundle.crt"
        actual.write_bytes(b"preserved external trust")
        bundle.symlink_to(actual)
    query = tmp_path / "fixture-dpkg-query"
    query.write_text(
        "#!/bin/sh\n"
        + ("exit 1\n" if package == "absent" else f"printf '%s' {shlex.quote(package)}\n")
    )
    query.chmod(0o700)
    source = (ROOT / "setup.sh").read_text()
    start = source.index("setup_ca_ready() {")
    function = source[start : source.index("\nsetup_prerequisite_files()", start)]
    function = function.replace("/etc/ssl/certs/ca-certificates.crt", shlex.quote(str(bundle)))
    function = function.replace("/usr/bin/dpkg-query", shlex.quote(str(query)))

    def snapshot() -> dict[Path, tuple[int, ...]]:
        result = {}
        for path in tmp_path.iterdir():
            info = path.lstat()
            # Executing the query fixture can update atime without writing its content.
            result[path] = (
                info.st_mode,
                info.st_ino,
                info.st_dev,
                info.st_nlink,
                info.st_uid,
                info.st_gid,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )
        return result

    before = snapshot()
    result = subprocess.run(
        ["/usr/bin/bash", "-c", function + "\nsetup_ca_ready\n"],
        env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        text=True,
        capture_output=True,
        timeout=3,
        check=False,
    )
    assert (result.returncode == 0) is ready, result.stderr
    assert snapshot() == before


@pytest.mark.parametrize("repair", ["success", "failure", "no-bundle"])
def test_signed_bootstrap_rebuilds_missing_ca_and_rechecks_before_handoff(
    tmp_path: Path, repair: str
) -> None:
    stage = tmp_path / "staging"
    stage.mkdir()
    bundle = tmp_path / "ca bundle.crt"
    keyring = tmp_path / "debian-archive-keyring.pgp"
    keyring.write_bytes(b"fixture Debian archive trust anchor")
    query = tmp_path / "fixture-query"
    query.write_text("#!/bin/sh\nprintf installed\n")
    query.chmod(0o700)
    repair_tool = tmp_path / "fixture-update-ca-certificates"
    repair_tool.write_text("#!/bin/sh\nexit 99\n")
    repair_tool.chmod(0o700)
    log = tmp_path / "external-calls.jsonl"
    timeout = tmp_path / "fixture-timeout"
    timeout.write_text(
        "#!" + sys.executable + "\n"
        "import json, pathlib, sys\n"
        f"log = pathlib.Path({str(log)!r})\n"
        "args = sys.argv[1:]\n"
        "with log.open('a') as stream: stream.write(json.dumps(args) + '\\n')\n"
        f"if {str(repair_tool)!r} in args:\n"
        f"    if {repair!r} == 'failure': sys.exit(1)\n"
        f"    if {repair!r} == 'success':\n"
        f"        pathlib.Path({str(bundle)!r}).write_bytes(b'trust fixture')\n"
    )
    timeout.chmod(0o700)
    source = (ROOT / "setup.sh").read_text()
    start = source.index("setup_ca_ready() {")
    function = source[start : source.index("\nsetup_handoff()", start)]
    for original, replacement in (
        ("/etc/ssl/certs/ca-certificates.crt", bundle),
        ("/usr/share/keyrings/debian-archive-keyring.pgp", keyring),
        ("/usr/bin/dpkg-query", query),
        ("/usr/bin/timeout", timeout),
        ("/usr/sbin/update-ca-certificates", repair_tool),
        ("/run/setup-system-prerequisites.XXXXXXXX", stage / "prerequisites.XXXXXXXX"),
    ):
        function = function.replace(original, shlex.quote(str(replacement)))
    script = (
        f"source {shlex.quote(str(ROOT / 'setup.sh'))}\n"
        f"setup_root={shlex.quote(str(ROOT))}\n" + function + "\nsetup_python_prerequisite\n"
    )
    result = subprocess.run(
        ["/usr/bin/bash", "-c", script],
        env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == 4 and all("/usr/bin/apt-get" in call for call in calls[:3])
    assert calls[3][-1] == str(repair_tool)
    assert calls[3][calls[3].index("/usr/bin/env") + 1 : -1] == [
        "-i",
        "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG=C",
        "LC_ALL=C",
    ]
    assert "--fresh" not in calls[3] and "--default" not in calls[3]
    assert result.returncode == (0 if repair == "success" else 5), result.stderr
    assert bundle.exists() is (repair == "success")
    if repair == "no-bundle":
        assert "CA bundle is still unavailable" in result.stderr
    assert keyring.read_bytes() == b"fixture Debian archive trust anchor"
    assert not list(stage.iterdir())


@pytest.mark.parametrize("python", [False, True])
@pytest.mark.parametrize("arguments", [(), ("--yes",)])
def test_missing_ca_no_tty_rejects_missing_consent_or_file_before_bootstrap(
    tmp_path: Path, python: bool, arguments: tuple[str, ...]
) -> None:
    result = invoke(tmp_path, *arguments, python=python, ca=False)
    assert result.process.returncode == 2
    assert "No TTY" in result.process.stderr and not result.calls
    assert list(result.home.iterdir()) == []
