"""Open a native account client in a foreground terminal on the local Wayland session."""

import os
import stat
import subprocess
from pathlib import Path

from setup_system.authorization import session_environment
from setup_system.infrastructure.system_paths import protected

PERMISSION = "native-terminal"
_FOOT = Path("/usr/bin/foot")


def available() -> bool:
    """Inspect the packaged terminal and local display without opening a window."""
    if os.geteuid() == 0:
        return False
    display = os.environ.get("WAYLAND_DISPLAY", "")
    root = Path(os.environ.get("XDG_RUNTIME_DIR", ""))
    if not display or Path(display).name != display or not root.is_absolute():
        return False
    try:
        protected(_FOOT, directory=False)
        folder = root.lstat()
        endpoint = (root / display).lstat()
        return (
            os.access(_FOOT, os.X_OK)
            and _FOOT.is_file()
            and root.resolve() == root
            and stat.S_ISDIR(folder.st_mode)
            and folder.st_uid == os.getuid()
            and not folder.st_mode & 0o077
            and stat.S_ISSOCK(endpoint.st_mode)
            and endpoint.st_uid == os.getuid()
        )
    except (OSError, ValueError):
        return False


def run_native(argv: list[str], *, cwd: Path | None = None) -> int:
    """Wait for the client. Its terminal owns all prompts, codes and secret input."""
    if os.environ.get("SETUP_SYSTEM_NATIVE_TERMINAL") != "1" or not available():
        raise ValueError(
            "A confirmed native terminal action and local Wayland session are required"
        )
    program = Path(argv[0]) if argv else Path()
    if not program.is_absolute() or not program.is_file() or not os.access(program, os.X_OK):
        raise ValueError("The selected native client is unavailable")
    if any("\x00" in argument for argument in argv):
        raise ValueError("Invalid native argument")
    if cwd is not None and (not cwd.is_absolute() or cwd.resolve() != cwd or not cwd.is_dir()):
        raise ValueError("The native working directory is unavailable")
    # Inherit the Make process group so the host owns the terminal's lifetime and deadline.
    return subprocess.run(
        [str(_FOOT), "--config=/dev/null", "--", *argv],
        cwd=cwd or Path.home(),
        env=session_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode
