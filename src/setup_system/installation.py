"""Stdlib-only identity, signed APT and privilege boundaries for the one-command installer."""

from __future__ import annotations

import contextlib
import grp
import hashlib
import json
import os
import platform
import pwd
import re
import signal
import stat
import subprocess
import tempfile
import tomllib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from setup_system.adapters.debian.hyprland import package_arguments, profile_capabilities
from setup_system.planning import load_capabilities


@dataclass(frozen=True)
class TargetUser:
    name: str
    uid: int
    gid: int
    home: Path
    shell: str


@dataclass(frozen=True)
class SystemPaths:
    backports: Path = Path("/etc/apt/sources.list.d/setup-system-backports.sources")
    journal: Path = Path("/var/lib/setup-system/install.json")
    keyring: Path = Path("/usr/share/keyrings/debian-archive-keyring.pgp")
    runtime: Path = Path("/run")
    policy: Path = Path("/usr/sbin/policy-rc.d")
    happ_unit: Path = Path("/etc/systemd/system/happd.service")
    happ_runtime_unit: Path = Path("/run/systemd/system/happd.service")


def validate_user_path(path: Path, target: TargetUser) -> Path:
    if (
        not path.is_absolute()
        or ".." in path.parts
        or "." in str(path).split("/")
        or any(ord(c) < 32 for c in str(path))
        or not path.is_relative_to(target.home)
    ):
        raise ValueError("Managed paths must stay inside the verified HOME without traversal")
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("Managed paths must not traverse symlinks")
        if parent != path and parent.exists() and not parent.is_dir():
            raise ValueError("Managed parent is not a directory")
        if parent.exists() and parent != target.home and parent.is_relative_to(target.home):
            if parent.stat().st_uid != target.uid:
                raise ValueError("Managed user path has a different owner")
    return path


def validate_home(target: TargetUser) -> None:
    if target.home in {Path("/"), Path("/root")} or not target.home.is_dir():
        raise ValueError("Target HOME must exist and must not be root HOME")
    validate_user_path(target.home, target)
    if target.home.stat().st_uid != target.uid:
        raise ValueError("Target HOME must be owned by the target user")
    if Path(target.shell).name in {"false", "nologin"}:
        raise ValueError("Target account must support an interactive login")


def resolve_target(
    name: str | None,
    *,
    environment: Mapping[str, str] | None = None,
    effective_uid: int | None = None,
    real_uid: int | None = None,
) -> TargetUser:
    env = os.environ if environment is None else environment
    euid = os.geteuid() if effective_uid is None else effective_uid
    uid = os.getuid() if real_uid is None else real_uid
    sudo = env.get("SUDO_USER")
    if sudo and sudo != "root":
        if not env.get("SUDO_UID") or not env.get("SUDO_GID") or (name and name != sudo):
            raise ValueError("Incomplete or conflicting sudo identity")
        name = sudo
    elif euid == 0 and not name:
        raise ValueError("Direct root invocation requires --user NAME")
    elif not name:
        name = pwd.getpwuid(uid).pw_name
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_.-]*\$?", name):
        raise ValueError("Invalid target user")
    try:
        record = pwd.getpwnam(name)
        grp.getgrgid(record.pw_gid)
    except KeyError as exc:
        raise ValueError("Unknown target account or primary group") from exc
    if record.pw_name != name or record.pw_uid == 0 or record.pw_gid < 0:
        raise ValueError("Root/ambiguous accounts cannot be the desktop target")
    if euid != 0 and record.pw_uid != uid:
        raise ValueError("An unprivileged invocation can configure only the current user")
    if (
        sudo
        and sudo != "root"
        and (env["SUDO_UID"] != str(record.pw_uid) or env["SUDO_GID"] != str(record.pw_gid))
    ):
        raise ValueError("SUDO_UID/SUDO_GID do not match the passwd account")
    target = TargetUser(name, record.pw_uid, record.pw_gid, Path(record.pw_dir), record.pw_shell)
    validate_home(target)
    return target


def user_environment(target: TargetUser, overrides: Mapping[str, Path]) -> dict[str, str]:
    defaults = {
        "config": ".config",
        "data": ".local/share",
        "state": ".local/state",
        "cache": ".cache",
    }
    if set(overrides) - defaults.keys():
        raise ValueError("Unknown XDG override")
    env = {
        "HOME": str(target.home),
        "USER": target.name,
        "LOGNAME": target.name,
        "SHELL": target.shell,
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for key, suffix in defaults.items():
        path = validate_user_path(overrides.get(key, target.home / suffix), target)
        if path == target.home:
            raise ValueError("XDG roots must be subdirectories of HOME")
        env[f"XDG_{key.upper()}_HOME"] = str(path)
    return env


def validate_installation_paths(
    root: Path, target: TargetUser, environment: Mapping[str, str]
) -> None:
    """Reject unsafe known managed destinations before any package/runtime mutation."""
    specifications = list(cast(list[str], profile_capabilities(root)["preflight_paths"]))
    for source in (root / "assets/hyprland").rglob("*"):
        if source.is_file() and source.name != "start-hyprland":
            specifications.append(
                "config:hypr/setup-system/" + str(source.relative_to(root / "assets/hyprland"))
            )
    font = tomllib.loads((root / "manifests/fonts.toml").read_text())["jetbrains_mono_nerd_font"]
    font_root = "data:fonts/JetBrainsMonoNerdFont-" + font["version"] + "/"
    specifications.append(font_root)
    specifications.extend(font_root + item["name"] for item in font["files"])
    for specification in specifications:
        kind, relative = specification.split(":", 1)
        if kind not in {"config", "data", "state", "cache"} or Path(relative).is_absolute():
            raise ValueError("Invalid managed destination in capability manifest")
        path = validate_user_path(Path(environment[f"XDG_{kind.upper()}_HOME"]) / relative, target)
        if path.exists() and (not path.is_dir() if relative.endswith("/") else not path.is_file()):
            raise ValueError("Managed destination has an unsafe type")


def _system_path(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Unsafe system path")
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("System path traverses a symlink")
    existing = path
    while not existing.exists():
        existing = existing.parent
    details = existing.stat()
    if details.st_uid != os.geteuid() or details.st_mode & 0o022:
        raise ValueError("System target/parent is not privately controlled by this user")
    if existing == path and not stat.S_ISREG(details.st_mode):
        raise ValueError("System file is not regular")
    directory = path.parent
    while not directory.exists():
        directory = directory.parent
    parent_details = directory.stat()
    if parent_details.st_uid != os.geteuid() or parent_details.st_mode & 0o022:
        raise ValueError("System parent is not privately controlled by this user")


def _write_system(path: Path, content: bytes, mode: int = 0o600) -> None:
    _system_path(path)
    if path.exists() and path.read_bytes() == content:
        return
    missing: list[Path] = []
    directory = path.parent
    while not directory.exists():
        missing.append(directory)
        directory = directory.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        # Only directories just created below the validated parent are changed.
        directory.chmod(0o700)
    _system_path(path)
    descriptor, name = tempfile.mkstemp(prefix=".setup-system-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def validate_simulation(output: str) -> None:
    if re.search(r"(?mi)^Remv\s|\bDOWNGRADED\b|\bwill be REMOVED\b", output):
        raise RuntimeError("APT proposed removal/downgrade; no package changes authorized")


DEFER_POLICY = b"#!/bin/sh\n# setupSystem temporary package-stage policy\nexit 101\n"


def _check_service_policy(path: Path) -> None:
    _system_path(path)
    if path.exists() and path.read_bytes() not in {
        DEFER_POLICY,
        b"#!/bin/sh\nexit 101\n",
        b"#!/bin/sh\nexit 101",
    }:
        raise ValueError("Existing service policy needs review before safe package deferral")
    if (
        path.exists()
        and path.read_bytes() != DEFER_POLICY
        and not path.stat().st_mode & stat.S_IXUSR
    ):
        raise ValueError("Existing service policy is not executable; review it before installation")


@contextlib.contextmanager
def _defer_services(path: Path) -> Iterator[None]:
    # Preserve an administrator's policy. A temporary policy prevents SSH/network restarts.
    _check_service_policy(path)
    content = DEFER_POLICY
    created = path.exists() and path.read_bytes() == content
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o755)
        created = True
    elif created:
        # Recognized interrupted setupSystem policy; restore its usable executable mode.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fchmod(descriptor, 0o755)
        finally:
            os.close(descriptor)
    try:
        yield
    finally:
        if created:
            _system_path(path)
            if path.read_bytes() != content:
                raise RuntimeError("Temporary service policy changed; review it before retrying")
            path.unlink()


def run_command(
    argv: list[str],
    *,
    timeout: int = 180,
    termination_grace: int = 5,
    check: bool = True,
    capture_output: bool = True,
    text: bool = True,
    input: str | None = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Bound each process group and propagate cancellation through nested supervisors."""
    if not capture_output or not text:
        raise ValueError("Installer command capture must be textual")
    if input is not None:
        if "stdin" in kwargs:
            raise ValueError("Installer input cannot be combined with a separate stdin")
        kwargs["stdin"] = subprocess.PIPE

    def interrupted(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt("Installer command interrupted")

    previous = signal.signal(signal.SIGTERM, interrupted)
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            argv,
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **kwargs,
        )
        try:
            stdout, stderr = process.communicate(input=input, timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            # A parent may exit and close its pipes while a TERM-ignoring child lives.
            # Always escalate the remaining group, even when communicate returns early.
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=termination_grace)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            raise
        result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
        if check and result.returncode:
            raise subprocess.CalledProcessError(result.returncode, argv, stdout, stderr)
        return result
    finally:
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        signal.signal(signal.SIGTERM, previous)


def validate_source_access(
    root: Path,
    target: TargetUser,
    paths: list[tuple[Path, bool]],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = run_command,
) -> None:
    """Probe bounded build inputs with the target account and trusted system Python."""
    if target.uid <= 0 or target.gid < 0:
        raise ValueError("Source access requires a non-root target account")
    try:
        account = pwd.getpwnam(target.name)
    except KeyError as exc:
        raise ValueError("Source access target account does not exist") from exc
    if account.pw_name != target.name or (account.pw_uid, account.pw_gid) != (
        target.uid,
        target.gid,
    ):
        raise ValueError("Source access target UID/GID do not match the passwd account")
    privileged = os.geteuid() == 0
    if not privileged and (target.uid != os.getuid() or target.uid != os.geteuid()):
        raise ValueError("An unprivileged source probe can inspect only the current account")
    if (
        not root.is_absolute()
        or ".." in root.parts
        or any(ord(character) < 32 or 127 <= ord(character) < 160 for character in str(root))
    ):
        raise ValueError("Source root must be a safe absolute path")
    if len(paths) > 4096:
        raise ValueError("Source access input exceeds its entry limit")
    entries: list[tuple[str, bool]] = []
    seen: set[Path] = set()
    for relative, directory in paths:
        if (
            not isinstance(relative, Path)
            or relative.is_absolute()
            or relative == Path(".")
            or ".." in relative.parts
            or type(directory) is not bool
            or relative in seen
            or len(str(relative)) > 4096
            or any(
                ord(character) < 32 or 127 <= ord(character) < 160 for character in str(relative)
            )
        ):
            raise ValueError("Source access needs unique safe relative files and directories")
        seen.add(relative)
        entries.append((str(relative), directory))
    payload = json.dumps({"root": str(root), "paths": entries})
    if len(payload) > 1024 * 1024:
        raise ValueError("Source access input exceeds its serialized size limit")
    # All executable code is fixed. Source paths are bounded JSON data on stdin.
    program = r"""
import json
import os
from pathlib import Path
import stat
import sys


def deny(reason):
    print(json.dumps({"error": reason}))
    raise SystemExit(1)


text = sys.stdin.read(1024 * 1024 + 1)
if len(text) > 1024 * 1024:
    deny("source input exceeds its size limit")
request = json.loads(text)
root = Path(request["root"])
if not os.access("/usr/bin/python3", os.X_OK, effective_ids=True):
    deny("system Python is not executable")
if not root.is_dir() or not os.access(root, os.R_OK | os.X_OK, effective_ids=True):
    deny("source directory is not readable/searchable: " + str(root))
for path in (root, *root.parents):
    if path.is_symlink():
        deny("source root traverses a symlink")
for relative, directory in request["paths"]:
    path = root / relative
    for parent in (path, *path.parents):
        if parent.is_symlink():
            deny("source path traverses a symlink: " + relative)
        if parent == root:
            break
    permissions = os.R_OK | os.X_OK if directory else os.R_OK
    if not os.access(path, permissions, effective_ids=True):
        deny("source path is not readable/searchable: " + relative)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            mode = os.fstat(descriptor).st_mode
            if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
                deny("source path has an unexpected file/directory type: " + relative)
        finally:
            os.close(descriptor)
    except OSError:
        deny("source path is not accessible: " + relative)
print(json.dumps({"ok": True}))
"""
    options: dict[str, Any] = {
        "cwd": Path("/"),
        "env": {
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "HOME": str(target.home),
            "USER": target.name,
            "LOGNAME": target.name,
            "LANG": "C",
            "LC_ALL": "C",
        },
        "input": payload,
        "umask": 0o077,
        "timeout": 30,
        "check": False,
    }
    if privileged:
        options.update(
            user=target.uid,
            group=target.gid,
            extra_groups=os.getgrouplist(target.name, target.gid),
        )
    try:
        result = run(["/usr/bin/python3", "-I", "-B", "-c", program], **options)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(
            "Target user source access probe could not run with system Python"
        ) from exc
    try:
        response = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "System Python source probe did not return a valid acknowledgement"
        ) from exc
    if result.returncode or response != {"ok": True}:
        detail = response.get("error") if isinstance(response, dict) else None
        message = "Source bundle is not accessible to the target user"
        if isinstance(detail, str) and len(detail) <= 8192:
            message += ": " + detail
        raise ValueError(message)


@dataclass
class PreparedPackages:
    """Keep one prepared package selection alive until its offline application finishes."""

    _apply: Callable[[], None]
    _activate: Callable[[], None]
    _active: bool = True
    _installed: bool = False
    _activated: bool = False

    def install(self) -> None:
        if not self._active:
            raise RuntimeError("Prepared package context is closed")
        if not self._installed:
            self._apply()
            self._installed = True

    def activate_happ(self) -> None:
        """Activate the native vendor helper after the caller closes temporary networking."""
        if not self._active:
            raise RuntimeError("Prepared package context is closed")
        if not self._installed:
            raise RuntimeError("Offline packages must be installed before Happ activation")
        if not self._activated:
            self._activate()
            self._activated = True


def validate_service_manager(
    *,
    root: Path | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = run_command,
) -> None:
    """Require the matrix's reviewed manager API without changing units or PID 1."""
    profile = profile_capabilities(root)
    expected = profile.get("init_version")
    if profile.get("init_system") != "systemd" or expected != "257":
        raise ValueError("Happ service controls are reviewed for systemd 257 only")
    environment = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}
    try:
        client = run(
            ["/usr/bin/systemctl", "--version"],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        if (
            client.returncode
            or re.match(r"systemd " + re.escape(expected) + r"(?:[ .(]|$)", client.stdout) is None
        ):
            raise ValueError("Happ requires the reviewed Debian systemd 257 client")
        manager = run(
            [
                "/usr/bin/systemctl",
                "--system",
                "--no-pager",
                "--no-ask-password",
                "show",
                "--property=Version",
                "--value",
            ],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        if (
            manager.returncode
            or re.match(re.escape(expected) + r"(?:[.~-]|$)", manager.stdout.strip()) is None
        ):
            raise ValueError("Happ requires an accessible systemd 257 system manager as init")
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Happ requires systemctl and an accessible systemd 257 manager") from exc


def _check_happ_unit_policy(paths: SystemPaths) -> None:
    for path in (paths.happ_unit, paths.happ_runtime_unit):
        if path.is_symlink():
            raise ValueError(
                "Happ service is masked or redirected by an administrator; "
                "review it without overwriting"
            )
        if path.exists() and not path.is_file():
            raise ValueError(
                "Existing Happ service unit is not a regular administrator-controlled file"
            )
        _system_path(path)


def _check_existing_happ(
    expected_version: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = run_command,
) -> None:
    """Refuse implicit Happ upgrades or repairs that could disrupt an active connection."""
    message = (
        "Happ package state requires manual review before installation; "
        "only an absent package or the exact configured pinned release is supported"
    )
    environment = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}
    try:
        result = run(
            [
                "/usr/bin/dpkg-query",
                "--no-pager",
                "--show",
                r"--showformat=${Status}\n${Version}\n",
                "happ",
            ],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        raise ValueError(message) from None
    if result.returncode == 1 and result.stdout == "":
        return
    if result.returncode == 0 and result.stdout == f"install ok installed\n{expected_version}\n":
        return
    raise ValueError(message)


def _loopback_proxy(proxy: str | None) -> str | None:
    if proxy is None:
        return None
    try:
        parsed = urlsplit(proxy)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid download proxy") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or not 1024 <= port <= 65535
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or any(ord(character) <= 32 or ord(character) >= 127 for character in proxy)
    ):
        raise ValueError("Download proxy must be credential-free loopback HTTP on a high port")
    host = "[::1]" if parsed.hostname == "::1" else "127.0.0.1"
    return f"http://{host}:{port}"


def _copy_happ(source: Path, target: TargetUser, destination: Path, digest: str) -> None:
    """Read a bounded regular user file once and verify the privately staged bytes."""
    validate_user_path(source, target)
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != target.uid
            or not 0 < details.st_size <= 200 * 1024 * 1024
        ):
            raise ValueError(
                "Happ artifact must be a bounded regular file owned by the target user"
            )
        output = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        checksum = hashlib.sha256()
        total = 0
        with os.fdopen(output, "wb") as staged:
            os.fchmod(staged.fileno(), 0o600)
            while block := os.read(descriptor, 1024 * 1024):
                total += len(block)
                if total > 200 * 1024 * 1024:
                    raise ValueError("Happ artifact exceeds its size limit")
                checksum.update(block)
                staged.write(block)
            staged.flush()
            os.fsync(staged.fileno())
        if total != details.st_size or checksum.hexdigest() != digest:
            raise ValueError("Happ artifact digest differs from the pinned release")
    finally:
        os.close(descriptor)


def _freeze_selection(arguments: list[str], output: str, happ_version: str) -> list[str]:
    """Retain explicit requests and constrain the simulated dependency closure exactly."""
    selected: dict[str, str] = {}
    for line in output.splitlines():
        if not line.startswith("Inst "):
            continue
        match = re.match(r"Inst (\S+)(?: \[[^]]+\])? \((\S+) ", line)
        if (
            match is None
            or not re.fullmatch(r"[a-z0-9][a-z0-9+.-]*(?::[a-z0-9][a-z0-9-]*)?", match[1])
            or not re.fullmatch(r"[A-Za-z0-9.+:~_-]+", match[2])
        ):
            raise RuntimeError("Cannot freeze an unrecognized APT simulation entry")
        name, version = match[1], match[2]
        if name in selected and selected[name] != version:
            raise RuntimeError("APT simulation selected conflicting package versions")
        if name.split(":", 1)[0] == "happ":
            if version != happ_version:
                raise RuntimeError("APT simulation selected an unexpected Happ version")
            continue
        selected[name] = version
    frozen: list[str] = []
    for argument in arguments:
        if argument.startswith("/"):
            frozen.append(argument)
            continue
        name, separator, pinned = argument.partition("=")
        choices = [key for key in selected if key == name or key.split(":", 1)[0] == name]
        if len(choices) == 1:
            version = selected.pop(choices[0])
            if separator and pinned != version:
                raise RuntimeError("APT simulation conflicts with an exact package pin")
            frozen.append(f"{name}={version}")
        else:
            frozen.append(argument)
    return [*frozen, *(f"{name}={version}" for name, version in selected.items())]


@contextlib.contextmanager
def prepare_packages(
    root: Path,
    *,
    target: TargetUser,
    happ: Path,
    proxy: str | None,
    run: Callable[..., subprocess.CompletedProcess[str]] = run_command,
    paths: SystemPaths | None = None,
    release: dict[str, str] | None = None,
    architecture: str | None = None,
    emit: Callable[[str], None] = print,
) -> Iterator[PreparedPackages]:
    """Download through an explicit proxy, then expose a fully offline package stage."""
    paths = SystemPaths() if paths is None else paths
    packages = package_arguments(root=root, os_release=release, architecture=architecture)
    profile = profile_capabilities(root)
    proxy = _loopback_proxy(proxy)
    aliases = cast(dict[str, str], load_capabilities(root)["architectures"])
    machine = architecture if architecture is not None else platform.machine()
    machine = aliases.get(machine, machine)
    apt_architecture = next(
        (
            item
            for item in cast(list[str], profile["apt_architectures"])
            if aliases.get(item, item) == machine
        ),
        None,
    )
    if apt_architecture is None:
        raise ValueError("No exact Debian package architecture matches this target")
    if target.uid <= 0:
        raise ValueError("Happ installation needs a verified non-root target user")
    validate_user_path(happ, target)
    if paths.keyring.is_symlink() or not paths.keyring.is_file():
        raise ValueError(f"Installed regular Debian archive keyring is required: {paths.keyring}")
    manifest_path = root / "manifests/happ.toml"
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or manifest_path.stat().st_size > 1024 * 1024
    ):
        raise ValueError("Happ manifest must be a bounded regular file")
    manifest = tomllib.loads(manifest_path.read_text())
    linux = manifest.get("linux")
    asset = linux.get(apt_architecture) if isinstance(linux, dict) else None
    if not isinstance(asset, dict):
        raise ValueError("Missing pinned Happ package for the target architecture")
    digest, happ_version = asset.get("sha256"), manifest.get("packageVersion")
    if (
        not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or not isinstance(happ_version, str)
        or re.fullmatch(r"[A-Za-z0-9.+:~_-]{1,200}", happ_version) is None
        or asset.get("packageArchitecture") != apt_architecture
    ):
        raise ValueError("Invalid pinned Happ digest, version or target architecture")
    codename, backports = str(profile["codename"]), str(profile["backports"])
    backport_source = (
        "# Managed by setupSystem; official Debian backports only.\n"
        "Types: deb\nURIs: http://deb.debian.org/debian\n"
        f"Suites: {backports}\nComponents: main\nSigned-By: {paths.keyring}\n"
    ).encode()
    _system_path(paths.backports)
    if paths.backports.exists() and paths.backports.read_bytes() != backport_source:
        raise ValueError("Existing backports file differs; review it without overwriting")
    _check_service_policy(paths.policy)
    _check_happ_unit_policy(paths)
    _system_path(paths.journal)
    if (
        not paths.runtime.is_absolute()
        or any(parent.is_symlink() for parent in (paths.runtime, *paths.runtime.parents))
        or not paths.runtime.is_dir()
    ):
        raise ValueError("APT staging needs a regular trusted runtime directory")
    runtime_details = paths.runtime.stat()
    if runtime_details.st_uid != os.geteuid() or runtime_details.st_mode & 0o022:
        raise ValueError("APT staging directory is not privately controlled")
    environment = {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "DEBIAN_FRONTEND": "noninteractive",
    }

    def execute(
        argv: list[str],
        timeout: int = 180,
        *,
        manager_offline: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        command_environment = (
            {**environment, "SYSTEMD_OFFLINE": "1"} if manager_offline else environment
        )
        result = run(
            argv,
            env=command_environment,
            text=True,
            capture_output=True,
            check=True,
            timeout=timeout,
        )
        if result.returncode:
            raise subprocess.CalledProcessError(
                result.returncode, argv, result.stdout, result.stderr
            )
        return result

    validate_service_manager(root=root, run=run)
    _check_existing_happ(happ_version, run=run)
    try:
        execute(["/usr/bin/unshare", "--net", "--", "/usr/bin/true"], timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(
            "Offline package apply requires root network namespace support (unshare --net)"
        ) from exc
    with tempfile.TemporaryDirectory(prefix="setup-system-apt-", dir=paths.runtime) as temporary:
        stage = Path(temporary)
        stage.chmod(0o700)
        local_happ = stage / "happ.deb"
        _copy_happ(happ, target, local_happ, digest)
        metadata = execute(
            ["/usr/bin/dpkg-deb", "--field", str(local_happ), "Package", "Version", "Architecture"]
        )
        fields: dict[str, str] = {}
        for line in metadata.stdout.splitlines():
            name, separator, value = line.partition(":")
            if not separator or name in fields:
                raise ValueError("Happ archive metadata differs from the pinned release")
            fields[name] = value.strip()
        if fields != {"Package": "happ", "Version": happ_version, "Architecture": apt_architecture}:
            raise ValueError("Happ archive metadata differs from the pinned release")
        # Verified package bytes and signed metadata are public. Keep root-only writes,
        # but allow APT's native _apt sandbox to traverse its acquisition cache.
        local_happ.chmod(0o644)
        stage.chmod(0o755)
        state: dict[str, Any] = {
            "schema": 1,
            "status": "running",
            "stage": "metadata",
            "completed": [],
        }

        def checkpoint(next_stage: str, completed: str | None = None) -> None:
            if completed:
                state["completed"].append(completed)
            state["stage"] = next_stage
            _write_system(paths.journal, (json.dumps(state, indent=2) + "\n").encode())

        checkpoint("metadata")
        prepared: PreparedPackages | None = None
        try:
            for name in ("lists", "lists/partial", "archives", "archives/partial"):
                directory = stage / name
                directory.mkdir(mode=0o755)
                directory.chmod(0o755)
            # APT does not cache local .deb inputs during --download-only. Offline
            # installation needs its canonical archive name as well as the input path.
            cached_happ = stage / "archives" / f"happ_{happ_version}_{apt_architecture}.deb"
            os.link(local_happ, cached_happ, follow_symlinks=False)
            sources = stage / "debian.sources"
            sources.write_text(
                "Types: deb\nURIs: http://deb.debian.org/debian\n"
                f"Suites: {codename} {codename}-updates {backports}\n"
                f"Components: main\nSigned-By: {paths.keyring}\n\n"
                "Types: deb\nURIs: http://security.debian.org/debian-security\n"
                f"Suites: {codename}-security\nComponents: main\nSigned-By: {paths.keyring}\n"
            )
            sources.chmod(0o644)
            options = [
                "-o",
                f"Dir::Etc::sourcelist={sources}",
                "-o",
                "Dir::Etc::sourceparts=-",
                "-o",
                f"Dir::State::lists={stage / 'lists'}",
                "-o",
                f"Dir::Cache::archives={stage / 'archives'}",
                "-o",
                "Acquire::AllowInsecureRepositories=false",
                "-o",
                "APT::Get::AllowUnauthenticated=false",
                "-o",
                "APT::Update::Error-Mode=any",
                "-o",
                "Acquire::Retries=0",
                "-o",
                "Acquire::http::Timeout=30",
                "-o",
                "Acquire::https::Timeout=30",
                "-o",
                "DPkg::Lock::Timeout=30",
            ]
            online = [
                *options,
                "-o",
                f"Acquire::http::Proxy={proxy or 'DIRECT'}",
                "-o",
                f"Acquire::https::Proxy={proxy or 'DIRECT'}",
            ]
            install = [
                "--no-remove",
                "--no-install-recommends",
                "install",
                "python3",
                "ca-certificates",
                *packages,
                str(local_happ),
            ]
            emit("Stage: metadata (authorized signed Debian refresh)")
            execute(["/usr/bin/apt-get", *online, "update"])
            checkpoint("resolution", "metadata")
            simulation = execute(["/usr/bin/apt-get", *online, "--simulate", *install])
            validate_simulation(simulation.stdout)
            selection = _freeze_selection(install[3:], simulation.stdout, str(happ_version))
            install = [*install[:3], *selection]
            checkpoint("downloads", "resolution")
            emit("Stage: downloads (packages only; no installation or service changes)")
            execute(
                ["/usr/bin/apt-get", *online, "--download-only", "--yes", *install], timeout=1800
            )
            state["status"] = "prepared"
            checkpoint("user-prefetch", "downloads")

            def apply_offline() -> None:
                state["status"] = "running"
                checkpoint("packages")
                _check_existing_happ(happ_version, run=run)
                _check_happ_unit_policy(paths)
                offline = [
                    *options,
                    "-o",
                    "Acquire::http::Proxy=DIRECT",
                    "-o",
                    "Acquire::https::Proxy=DIRECT",
                ]
                emit(
                    "Stage: packages (offline network namespace; existing service starts deferred)"
                )
                with _defer_services(paths.policy):
                    execute(
                        [
                            "/usr/bin/unshare",
                            "--net",
                            "--",
                            "/usr/bin/apt-get",
                            *offline,
                            "--yes",
                            "--no-download",
                            *install,
                        ],
                        timeout=1800,
                        # systemd v257 skips online-only vendor commands; enablement stays local.
                        manager_offline=True,
                    )
                checkpoint("source", "packages")
                _write_system(paths.backports, backport_source, 0o644)
                state["status"] = "packages-complete"
                checkpoint("happ-service", "source")

            def activate_happ() -> None:
                state["status"] = "running"
                checkpoint("happ-service")
                _check_happ_unit_policy(paths)
                command = ["/usr/bin/systemctl", "--system", "--no-pager", "--no-ask-password"]
                try:
                    execute([*command, "daemon-reload"], timeout=30)
                    current = run(
                        [*command, "--quiet", "is-active", "happd.service"],
                        env=environment,
                        text=True,
                        capture_output=True,
                        check=False,
                        timeout=30,
                    )
                    if current.returncode not in {0, 3, 4}:
                        raise RuntimeError("Cannot inspect happd.service activation state")
                    if current.returncode:
                        execute([*command, "start", "happd.service"], timeout=60)
                    execute([*command, "--quiet", "is-active", "happd.service"], timeout=30)
                except (OSError, subprocess.SubprocessError) as exc:
                    raise RuntimeError(
                        "Vendor happd.service could not be activated; review its system journal"
                    ) from exc
                state["status"] = "system-complete"
                checkpoint("user-runtime", "happ-service")

            prepared = PreparedPackages(apply_offline, activate_happ)
            yield prepared
        except BaseException as exc:
            state["status"] = "failed"
            state["error"] = type(exc).__name__
            _write_system(paths.journal, (json.dumps(state, indent=2) + "\n").encode())
            raise
        finally:
            if prepared is not None:
                prepared._active = False


def mark_user_stage(
    paths: SystemPaths,
    status: str,
    *,
    stage: str | None = "user",
    completed: tuple[str, ...] = (),
) -> None:
    """Merge user progress into the latest package journal after its context has closed."""
    stages = {
        "metadata",
        "resolution",
        "downloads",
        "user-prefetch",
        "packages",
        "source",
        "happ-service",
        "user-runtime",
        "user",
        "user-downloads",
        "temporary-network",
        "package-downloads",
        "temporary-network-cleanup",
    }
    if status not in {"running", "complete", "failed"} or (
        stage is not None and stage not in stages
    ):
        raise ValueError("Invalid user installation stage or status")
    if any(item not in {"user-downloads", "user-configuration"} for item in completed):
        raise ValueError("Invalid completed user installation stage")
    _system_path(paths.journal)
    if paths.journal.stat().st_size > 1024 * 1024:
        raise ValueError("Installation journal exceeds its size limit")
    state = json.loads(paths.journal.read_text())
    if not isinstance(state, dict):
        raise ValueError("Installation journal must be an object")
    previous = state.get("completed", [])
    if not isinstance(previous, list) or any(not isinstance(item, str) for item in previous):
        raise ValueError("Installation journal has invalid completed stages")
    if stage is None:
        stage = state.get("stage")
        if not isinstance(stage, str) or stage not in stages:
            raise ValueError("Installation journal has an unknown current stage")
    state["completed"] = list(dict.fromkeys([*previous, *completed]))
    state["stage"] = stage
    state["status"] = status
    _write_system(paths.journal, (json.dumps(state, indent=2) + "\n").encode())


def current_packages(root: Path) -> list[str]:
    return package_arguments(root=root, os_release=platform.freedesktop_os_release())
