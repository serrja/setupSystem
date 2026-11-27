"""Portable, explicit Zsh configuration for the current account."""

from __future__ import annotations

import os
import platform
import pwd
import shlex
import shutil
import tempfile
from pathlib import Path

from setup_system_desktop_theme.palette import palette
from setup_system_sdk.files.file_publication import observe
from setup_system_sdk.files.managed_files import apply_files, changed_files
from setup_system_sdk.files.managed_text import managed_block
from setup_system_sdk.files.receipts import FileState, digest
from setup_system_sdk.files.templates import render_asset
from setup_system_sdk.files.user_storage import target_path
from setup_system_sdk.processes import run_command

from setup_plugin_zsh.facts import ShellStatus


def inspect_shell() -> ShellStatus:
    """Read the real account record and the host's registered shells."""
    if platform.system() != "Linux" or os.getuid() == 0:
        raise ValueError("User Zsh apply requires a non-root Linux account")
    account = pwd.getpwuid(os.getuid())
    home = Path(account.pw_dir).resolve()
    zdotdir = os.environ.get("ZDOTDIR")
    startup_dir = Path(zdotdir).expanduser().resolve() if zdotdir else home
    if not startup_dir.is_relative_to(home) or not startup_dir.is_dir():
        raise ValueError("ZDOTDIR must be an existing directory inside the current home")
    command = shutil.which("zsh")
    zsh = Path(command).absolute() if command else None
    if zsh is not None and (not zsh.is_file() or not os.access(zsh, os.X_OK)):
        zsh = None
    registered = False
    if zsh is not None:
        registered = str(zsh) in {
            line.strip()
            for line in Path("/etc/shells").read_text().splitlines()
            if line.strip() and not line.startswith("#")
        }
    return ShellStatus(
        user=account.pw_name,
        home=home,
        startup_dir=startup_dir,
        login_shell=Path(account.pw_shell),
        zsh=zsh,
        registered=registered,
    )


def _targets(
    root: Path,
    status: ShellStatus,
    *,
    expected: dict[Path, FileState | None] | None = None,
    config_home: Path | None = None,
) -> dict[Path, bytes]:
    assets = root / "assets"
    if not (assets / "init.zsh").is_file():
        raise ValueError("Installed setupSystem package has no Zsh assets")
    config_home = config_home or Path(os.environ.get("XDG_CONFIG_HOME") or status.home / ".config")
    if not config_home.resolve().is_relative_to(status.home):
        raise ValueError("XDG_CONFIG_HOME outside home requires a scoped install")
    targets = {
        config_home / "zsh" / source.name: (
            render_asset(source, palette()) if source.name == "prompt.zsh" else source.read_bytes()
        )
        for source in assets.glob("*.zsh")
    }
    startup = {
        ".zshenv": ": 'setupSystem: no non-interactive initialization'",
        ".zprofile": (
            "if [[ -r ${XDG_CONFIG_HOME:-$HOME/.config}/zsh/environment.zsh ]]; then "
            'source "${XDG_CONFIG_HOME:-$HOME/.config}/zsh/environment.zsh"; fi'
        ),
        ".zshrc": (
            "if [[ -r ${XDG_CONFIG_HOME:-$HOME/.config}/zsh/init.zsh ]]; then "
            'source "${XDG_CONFIG_HOME:-$HOME/.config}/zsh/init.zsh"; fi'
        ),
    }
    for name, body in startup.items():
        path = status.startup_dir / name
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError(f"Startup target is not a regular file: {path}")
        if expected is not None:
            target_path(path.relative_to(status.home).as_posix())
            expected[path] = observe(path)
        original = path.read_bytes() if path.exists() else None
        if expected is not None:
            prior = expected[path]
            if (digest(original) if original is not None else None) != (
                prior.sha256 if prior else None
            ):
                raise ValueError("Shell startup changed while preparing its managed block")
        targets[path] = managed_block((original or b"").decode(), body).encode()
    for path in targets:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError(f"Managed shell target is not a regular file: {path}")
    return targets


def _snapshot(
    root: Path, status: ShellStatus
) -> tuple[dict[Path, bytes], dict[Path, FileState | None], set[Path]]:
    expected: dict[Path, FileState | None] = {}
    targets = _targets(root, status, expected=expected)
    return targets, expected, set(changed_files(targets, expected=expected))


def plan_shell(root: Path) -> tuple[ShellStatus, list[tuple[Path, str]]]:
    """Check current ownership and compare desired bytes without writing or downloading."""
    status = inspect_shell()
    targets, _, changes = _snapshot(root, status)
    return status, [
        (path, "change" if path in changes else "unchanged") for path in sorted(targets)
    ]


def validate_shell(
    root: Path,
    zsh: Path,
    flags: tuple[str, ...] = ("-c", "-ic", "-lic"),
    terminal: str = "xterm-256color",
) -> None:
    """Exercise interactive and login startup with isolated user directories."""
    with tempfile.TemporaryDirectory(prefix="setup-shell-") as temporary:
        home = Path(temporary)
        config = home / ".config/zsh"
        config.mkdir(parents=True)
        install = zsh.resolve().parents[2]
        if (install / "usr/share/zsh/functions").is_dir():
            link = home / ".local/opt/zsh"
            link.parent.mkdir(parents=True)
            link.symlink_to(install)
        (home / ".local/state/zsh").mkdir(parents=True)
        isolated = ShellStatus(
            user="test",
            home=home,
            startup_dir=home,
            login_shell=Path("/bin/sh"),
            zsh=zsh,
            registered=False,
        )
        for path, content in _targets(root, isolated, config_home=home / ".config").items():
            path.write_bytes(content)
            run_command([str(zsh), "-f", "-n", str(path)], discard_output=True, timeout=10)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LC_ALL": "C.UTF-8",
            "TERM": terminal,
        }
        env.update(
            HOME=str(home),
            ZDOTDIR=str(home),
            XDG_CONFIG_HOME=str(home / ".config"),
            XDG_STATE_HOME=str(home / ".local/state"),
            XDG_CACHE_HOME=str(home / ".cache"),
            HISTFILE=str(home / ".local/state/zsh/history"),
        )
        for flag in flags:
            command = "print -r -- noninteractive" if flag == "-c" else "whence -w cproj compdef"
            invocation = [str(zsh), flag, command]
            if flag != "-c":
                script = shutil.which("script")
                if script is None:
                    raise RuntimeError("PTY-backed Zsh startup check requires script")
                invocation = [script, "-q", "-e", "-c", shlex.join(invocation), "/dev/null"]
            result = run_command(
                invocation,
                cwd=home,
                env=env,
                check=False,
                timeout=15,
            )
            output = result.stdout + result.stderr
            warning = next(
                (
                    item
                    for item in ("compinit:", "failed to load module", "not interactive")
                    if item in output
                ),
                None,
            )
            if result.returncode or warning:
                reason = warning or f"exit {result.returncode}"
                raise RuntimeError(f"Zsh isolated {flag} startup failed ({reason})")
            if flag != "-c" and not all(
                name + ": function" in result.stdout for name in ("cproj", "compdef")
            ):
                raise RuntimeError(
                    f"Zsh isolated {flag} did not load project navigation and completion"
                )


def history_directory(status: ShellStatus) -> Path:
    history_dir = Path(os.environ.get("XDG_STATE_HOME") or status.home / ".local/state") / "zsh"
    if not history_dir.resolve().is_relative_to(status.home):
        raise ValueError("XDG_STATE_HOME outside home requires a scoped install")
    if history_dir.is_symlink() or (history_dir.exists() and not history_dir.is_dir()):
        raise ValueError("Invalid Zsh history directory")
    return history_dir


def apply_shell(
    root: Path, *, reviewed: dict[Path, FileState | None] | None = None
) -> tuple[Path, list[tuple[Path, str]], ShellStatus]:
    """Preserve the current shell, history and text outside managed startup blocks."""
    status = inspect_shell()
    if status.zsh is None:
        raise ValueError("Installed Zsh executable is required")
    history_dir = history_directory(status)
    targets, expected, changes = _snapshot(root, status)
    if reviewed is not None:
        if targets.keys() != reviewed.keys() or any(
            observe(path) != state for path, state in reviewed.items()
        ):
            raise ValueError("Shell configuration changed after confirmation")
        expected = reviewed
    results = [(path, "changed" if path in changes else "unchanged") for path in targets]
    validate_shell(root, status.zsh)
    return apply_files(targets, expected=expected, directories=(history_dir,)), results, status
