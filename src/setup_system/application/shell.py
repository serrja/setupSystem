"""Portable, explicit Zsh configuration for the current account."""

from __future__ import annotations

import os
import platform
import pwd
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from setup_system.infrastructure.file_receipts import digest, target_path
from setup_system.infrastructure.user_files import apply_files
from setup_system.storage import managed_block


@dataclass(frozen=True)
class ShellStatus:
    """Observed host capability; no value is inferred from ``$SHELL``."""

    user: str
    home: Path
    startup_dir: Path
    login_shell: Path
    zsh: Path | None
    registered: bool


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
    return ShellStatus(account.pw_name, home, startup_dir, Path(account.pw_shell), zsh, registered)


def _targets(
    root: Path,
    status: ShellStatus,
    *,
    expected: dict[Path, str | None] | None = None,
    config_home: Path | None = None,
) -> dict[Path, bytes]:
    assets = root / "assets/zsh"
    if not (assets / "init.zsh").is_file():
        raise ValueError("Installed setupSystem package has no Zsh assets")
    config_home = config_home or Path(os.environ.get("XDG_CONFIG_HOME", status.home / ".config"))
    if not config_home.resolve().is_relative_to(status.home):
        raise ValueError("XDG_CONFIG_HOME outside home requires a scoped install")
    targets = {
        config_home / "zsh" / source.name: source.read_bytes() for source in assets.glob("*.zsh")
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
        original = path.read_bytes() if path.exists() else None
        if expected is not None:
            expected[path] = digest(original) if original is not None else None
        targets[path] = managed_block((original or b"").decode(), body).encode()
    for path in targets:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError(f"Managed shell target is not a regular file: {path}")
    return targets


def plan_shell(root: Path) -> tuple[ShellStatus, list[tuple[Path, str]]]:
    """Compare desired bytes with current files without writing or downloading."""
    status = inspect_shell()
    targets = _targets(root, status)
    changes = [
        (path, "unchanged" if path.exists() and path.read_bytes() == data else "change")
        for path, data in sorted(targets.items())
    ]
    return status, changes


def validate_shell(root: Path, zsh: Path) -> None:
    """Exercise interactive and login startup with isolated user directories."""
    assets = root / "assets/zsh"
    for source in assets.glob("*.zsh"):
        subprocess.run([str(zsh), "-f", "-n", str(source)], check=True, timeout=10)
    with tempfile.TemporaryDirectory(prefix="setup-shell-") as temporary:
        home = Path(temporary)
        config = home / ".config/zsh"
        config.mkdir(parents=True)
        for source in assets.glob("*.zsh"):
            shutil.copy2(source, config / source.name)
        install = Path.home() / ".local/opt/zsh"
        if install.is_dir():
            link = home / ".local/opt/zsh"
            link.parent.mkdir(parents=True)
            link.symlink_to(install)
        (home / ".local/state/zsh").mkdir(parents=True)
        isolated = ShellStatus("test", home, home, Path("/bin/sh"), zsh, False)
        for path, content in _targets(root, isolated, config_home=home / ".config").items():
            if path.parent == home:
                path.write_bytes(content)
        env = os.environ.copy()
        env.update(
            HOME=str(home),
            ZDOTDIR=str(home),
            XDG_CONFIG_HOME=str(home / ".config"),
            XDG_STATE_HOME=str(home / ".local/state"),
            XDG_CACHE_HOME=str(home / ".cache"),
            HISTFILE=str(home / ".local/state/zsh/history"),
        )
        for flags in ("-c", "-ic", "-lic"):
            command = (
                "print -r -- noninteractive"
                if flags == "-c"
                else "whence -w projectCd; whence -w compdef; whence -w git; whence -w nvim"
            )
            invocation = [str(zsh), flags, command]
            if flags != "-c":
                script = shutil.which("script")
                if script is None:
                    raise RuntimeError("PTY-backed Zsh startup check requires script")
                invocation = [script, "-q", "-e", "-c", shlex.join(invocation), "/dev/null"]
            result = subprocess.run(
                invocation,
                cwd=home,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            output = result.stdout + result.stderr
            if result.returncode != 0 or any(
                warning in output
                for warning in ("compinit:", "failed to load module", "not interactive")
            ):
                raise RuntimeError(f"Zsh isolated {flags} startup failed: {output.strip()}")
            if flags != "-c" and "projectCd: function" not in result.stdout:
                raise RuntimeError(f"Zsh isolated {flags} did not load project navigation")


def apply_shell(root: Path) -> tuple[Path, list[tuple[Path, str]], ShellStatus]:
    """Preserve the current shell, history and text outside managed startup blocks."""
    status = inspect_shell()
    if status.zsh is None:
        raise ValueError("Installed Zsh executable is required")
    history_dir = Path(os.environ.get("XDG_STATE_HOME", status.home / ".local/state")) / "zsh"
    if not history_dir.resolve().is_relative_to(status.home):
        raise ValueError("XDG_STATE_HOME outside home requires a scoped install")
    expected: dict[Path, str | None] = {}
    targets = _targets(root, status, expected=expected)
    results = [
        (path, "unchanged" if path.is_file() and path.read_bytes() == content else "changed")
        for path, content in targets.items()
    ]
    validate_shell(root, status.zsh)
    return apply_files(targets, expected=expected, directories=(history_dir,)), results, status
