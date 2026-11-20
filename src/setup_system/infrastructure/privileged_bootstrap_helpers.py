"""Validate the normal user's fallback editor without root plugin execution."""

import json
import subprocess
from pathlib import Path

from setup_system.infrastructure.trusted_bootstrap import caller


def editor_ready(username: str, config_home: Path | None = None) -> bool:
    account = caller()
    if username != account.pw_name:
        return False
    home = Path(account.pw_dir)
    binary = home / ".local/bin/nvim"
    if not binary.is_file():
        binary = Path("/usr/bin/nvim")
    elif not binary.resolve().is_relative_to(home):
        return False
    result = subprocess.run(
        ["/usr/sbin/runuser", "-u", username, "--", str(binary), "--clean", "--headless", "+q"],
        check=False,
        capture_output=True,
        timeout=30,
        env={"PATH": "/usr/bin:/bin", "HOME": account.pw_dir},
    )
    if result.returncode:
        return False
    path = (config_home or home / ".config") / "setup-system/editor.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_uid != account.pw_uid:
        return False
    data = json.loads(path.read_text())
    return bool(data.get("EDITOR") == "nvim" and data.get("VISUAL") == "nvim")
