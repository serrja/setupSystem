"""Portable, explicit Zsh configuration for the current account."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import pwd
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from setup_system.storage import atomic_write, managed_block, new_backup, state_root


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


def _targets(root: Path, status: ShellStatus) -> dict[Path, bytes]:
    assets = root / "assets/zsh"
    if not (assets / "init.zsh").is_file():
        raise ValueError("Installed setupSystem package has no Zsh assets")
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", status.home / ".config"))
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
        original = path.read_text() if path.exists() else ""
        targets[path] = managed_block(original, body).encode()
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


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
        for path, content in _targets(root, isolated).items():
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
    """Back up and install only the selected user's managed Zsh files."""
    status = inspect_shell()
    if status.zsh is None:
        raise ValueError("Installed Zsh executable is required")
    targets = _targets(root, status)
    history_dir = Path(os.environ.get("XDG_STATE_HOME", status.home / ".local/state")) / "zsh"
    if not history_dir.resolve().is_relative_to(status.home):
        raise ValueError("XDG_STATE_HOME outside home requires a scoped install")
    validate_shell(root, status.zsh)
    backup = new_backup(list(targets))
    record = {
        "backup_id": backup.name,
        "login_shell_before": str(status.login_shell),
        "zsh": str(status.zsh),
        "targets": {
            str(path): {"existed": path.exists(), "applied_sha256": _digest(data)}
            for path, data in targets.items()
        },
    }
    state_dir = state_root() / "shell-applies"
    state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    state_dir.chmod(0o700)
    results: list[tuple[Path, str]] = []
    for path, data in targets.items():
        results.append((path, atomic_write(path, data, 0o644)))
    history_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    history_dir.chmod(0o700)
    record_path = state_dir / f"{backup.name}.json"
    atomic_write(record_path, (json.dumps(record, indent=2) + "\n").encode())
    return backup, results, status


def change_login_shell(target: Path) -> tuple[bool, str]:
    """Use chsh for the current account after checking /etc/shells."""
    status = inspect_shell()
    if status.login_shell == target:
        return True, "unchanged"
    allowed = {
        line.strip()
        for line in Path("/etc/shells").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    if str(target) not in allowed:
        return False, f"{target} is not registered in /etc/shells"
    result = subprocess.run(
        ["chsh", "-s", str(target), status.user],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    updated = inspect_shell().login_shell == target
    return updated, "changed" if updated else (
        result.stderr.strip() or "chsh did not change account"
    )


def restore_shell(backup_id: str) -> list[tuple[Path, str]]:
    """Restore only files still identical to this apply's managed output."""
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", backup_id):
        raise ValueError("Invalid shell backup ID")
    record = json.loads((state_root() / "shell-applies" / f"{backup_id}.json").read_text())
    if record["backup_id"] != backup_id:
        raise ValueError("Shell backup ID mismatch")
    backup = state_root() / "backups" / backup_id
    manifest = json.loads((backup / "manifest.json").read_text())
    originals = {entry["source"]: entry for entry in manifest["items"]}
    prepared: list[tuple[Path, bytes | None, int]] = []
    home = Path.home().resolve()
    for name, target_info in record["targets"].items():
        path = Path(name)
        if not path.is_absolute() or not path.is_relative_to(home):
            raise ValueError(f"Shell restore target is outside home: {path}")
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Managed file disappeared or became a symlink: {path}")
        if _digest(path.read_bytes()) != target_info["applied_sha256"]:
            raise ValueError(f"User changed file after apply; review before restoring: {path}")
        if target_info["existed"]:
            entry = originals[name]
            saved = backup / entry["backup"]
            content = saved.read_bytes()
            if _digest(content) != entry["sha256"]:
                raise ValueError(f"Backup checksum mismatch: {saved}")
            prepared.append((path, content, int(entry["mode"])))
        else:
            prepared.append((path, None, 0))
    results: list[tuple[Path, str]] = []
    for path, content, mode in prepared:
        if content is None:
            path.unlink()
        else:
            atomic_write(path, content, mode, preserve_existing_mode=False)
        results.append((path, "restored"))
    return results
