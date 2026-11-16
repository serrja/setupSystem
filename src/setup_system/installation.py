"""Stdlib-only identity, signed APT and privilege boundaries for the one-command installer."""

from __future__ import annotations

import contextlib
import grp
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

from setup_system.adapters.debian.hyprland import package_arguments, profile_capabilities


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
    keyring: Path = Path("/usr/share/keyrings/debian-archive-keyring.gpg")
    runtime: Path = Path("/run")
    policy: Path = Path("/usr/sbin/policy-rc.d")


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
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Bound each process group and propagate cancellation through nested supervisors."""
    if not capture_output or not text:
        raise ValueError("Installer command capture must be textual")

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
            stdout, stderr = process.communicate(timeout=timeout)
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
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        signal.signal(signal.SIGTERM, previous)


def install_system(
    root: Path,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = run_command,
    paths: SystemPaths | None = None,
    release: dict[str, str] | None = None,
    architecture: str | None = None,
) -> None:
    paths = SystemPaths() if paths is None else paths
    packages = package_arguments(root=root, os_release=release, architecture=architecture)
    profile = profile_capabilities(root)
    codename = str(profile["codename"])
    backports = str(profile["backports"])
    if paths.keyring.is_symlink() or not paths.keyring.is_file():
        raise ValueError("Installed Debian archive keyring is required")
    backport_source = (
        "# Managed by setupSystem; official Debian backports only.\n"
        "Types: deb\nURIs: http://deb.debian.org/debian\n"
        f"Suites: {backports}\nComponents: main\nSigned-By: {paths.keyring}\n"
    ).encode()
    _system_path(paths.backports)
    if paths.backports.exists() and paths.backports.read_bytes() != backport_source:
        raise ValueError("Existing backports file differs; review it without overwriting")
    _check_service_policy(paths.policy)
    _system_path(paths.journal)
    state: dict[str, Any] = {"schema": 1, "status": "running", "stage": "metadata", "completed": []}
    _write_system(paths.journal, (json.dumps(state, indent=2) + "\n").encode())
    environment = {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "DEBIAN_FRONTEND": "noninteractive",
    }

    def execute(argv: list[str], timeout: int = 180) -> subprocess.CompletedProcess[str]:
        return run(
            argv, env=environment, text=True, capture_output=True, check=True, timeout=timeout
        )

    def checkpoint(stage: str, completed: str | None = None) -> None:
        if completed:
            state["completed"].append(completed)
        state["stage"] = stage
        _write_system(paths.journal, (json.dumps(state, indent=2) + "\n").encode())

    try:
        with tempfile.TemporaryDirectory(
            prefix="setup-system-apt-", dir=paths.runtime
        ) as temporary:
            stage = Path(temporary)
            stage.chmod(0o755)
            for name in ("lists/partial", "archives/partial"):
                (stage / name).mkdir(parents=True, mode=0o755)
            sources = stage / "debian.sources"
            # A private source list ignores third-party/trusted=yes sources on the host.
            sources.write_text(
                "Types: deb\nURIs: http://deb.debian.org/debian\n"
                f"Suites: {codename} {codename}-updates {backports}\n"
                f"Components: main\nSigned-By: {paths.keyring}\n\n"
                "Types: deb\nURIs: http://security.debian.org/debian-security\n"
                f"Suites: {codename}-security\nComponents: main\nSigned-By: {paths.keyring}\n"
            )
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
                "DPkg::Lock::Timeout=30",
            ]
            print("Stage: metadata (authorized signed Debian refresh)", flush=True)
            execute(["/usr/bin/apt-get", *options, "update"])
            checkpoint("resolution", "metadata")
            install = [
                "--no-remove",
                "--no-install-recommends",
                "install",
                "python3",
                "ca-certificates",
                *packages,
            ]
            simulation = execute(["/usr/bin/apt-get", *options, "--simulate", *install])
            validate_simulation(simulation.stdout)
            checkpoint("packages", "resolution")
            print("Stage: packages (no removals; existing service starts deferred)", flush=True)
            with _defer_services(paths.policy):
                execute(["/usr/bin/apt-get", *options, "--yes", *install], timeout=1800)
            checkpoint("source", "packages")
            # Persist only after APT verified signed metadata and installed the exact set.
            _write_system(paths.backports, backport_source, 0o644)
            state["status"] = "system-complete"
            checkpoint("user-runtime", "source")
    except BaseException as exc:
        state["status"] = "failed"
        state["error"] = type(exc).__name__
        _write_system(paths.journal, (json.dumps(state, indent=2) + "\n").encode())
        raise


def mark_user_stage(paths: SystemPaths, status: str) -> None:
    _system_path(paths.journal)
    state = json.loads(paths.journal.read_text())
    state["stage"] = "user"
    state["status"] = status
    _write_system(paths.journal, (json.dumps(state, indent=2) + "\n").encode())


def current_packages(root: Path) -> list[str]:
    return package_arguments(root=root, os_release=platform.freedesktop_os_release())
