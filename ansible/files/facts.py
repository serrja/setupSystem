"""Read target capabilities without opening a writer, elevated process or network."""

import json
import os
import pwd
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import TypedDict
from zoneinfo import available_timezones

from setup_system.infrastructure.native_target import current_target, get_release
from setup_system.infrastructure.package_journal import historical_status


class Display(TypedDict):
    output: str
    modes: list[str]


def keyboard_choices() -> list[str]:
    rules = Path("/usr/share/X11/xkb/rules/base.lst")
    layouts = []
    section = ""
    if rules.is_file():
        for line in rules.read_text().splitlines():
            if line.startswith("!"):
                section = line[1:].strip()
            elif line.strip() and section == "layout":
                layouts.append(line.split()[0])
    return sorted(set(layouts))


def monitors() -> list[Display]:
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or not Path("/usr/bin/hyprctl").is_file():
        return []
    try:
        probe = subprocess.run(
            ["/usr/bin/hyprctl", "monitors", "-j"], check=True, capture_output=True, timeout=4
        )
        rows = json.loads(probe.stdout)
        return [
            {
                "output": m["name"],
                "modes": [mode.removesuffix("Hz") for mode in m["availableModes"]],
            }
            for m in rows
        ]
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return []


def desktop_error() -> str | None:
    approved = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "config/base/desktop.toml").read_text()
    )
    probe = subprocess.run(
        ["/usr/bin/dpkg-query", "-W", "-f=${Status}\\t${Version}", "hyprland"],
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    match = re.fullmatch(
        r"install ok installed\s+(?:[0-9]+:)?(\d+)\.(\d+)\.[0-9]+\S*", probe.stdout.strip()
    )
    if probe.returncode or not match or tuple(map(int, match.groups())) < (0, 55):
        return "Install a Debian Hyprland release with Lua configuration support (0.55 or later)."
    missing = [
        name for name in approved["commands"] if not os.access(Path("/usr/bin") / name, os.X_OK)
    ]
    return (
        "Hyprland system stage is incomplete; missing: " + ", ".join(missing) if missing else None
    )


def main() -> None:
    if sys.argv[1:] == ["desktop-ready"]:
        issue = desktop_error()
        if issue:
            raise SystemExit(issue)
        return
    data = json.load(sys.stdin)
    if os.geteuid() == 0:
        raise ValueError("Open the local application as your ordinary account")
    home = Path(os.environ["HOME"])
    if not home.is_absolute() or not home.is_dir() or home.stat().st_uid != os.getuid():
        raise ValueError("HOME must be an existing directory owned by the caller")
    xdg = {}
    for name, suffix in (
        ("config", ".config"),
        ("data", ".local/share"),
        ("state", ".local/state"),
        ("cache", ".cache"),
    ):
        path = Path(os.environ.get("XDG_" + name.upper() + "_HOME", str(home / suffix)))
        if (
            ".." in path.parts
            or path == home
            or not path.is_relative_to(home)
            or any(p.is_symlink() for p in (path, *path.parents))
        ):
            raise ValueError("XDG directories must stay inside HOME without symlinks")
        xdg[name] = str(path)
    release = get_release()
    arch, supported = current_target(release)
    outputs = monitors()
    reason = [] if supported else ["System operations require Debian 13 trixie on amd64 or arm64."]
    intent = data["intent"]
    if any(
        not any(m["output"] == o["output"] and m["mode"] in o["modes"] for o in outputs)
        for m in intent.get("monitors", [])
    ):
        reason.append("A selected display or mode is unavailable in this Hyprland session.")
    if intent.get("graphical_login") and not intent.get("hyprland"):
        reason.append("Select Hyprland with graphical login.")
    if (intent.get("desktop_theme") or intent.get("monitors")) and supported:
        issue = desktop_error()
        if issue:
            reason.append(issue)
    account = pwd.getpwuid(os.getuid())
    result: dict[str, object] = dict(
        home=str(home),
        xdg=xdg,
        uid=account.pw_uid,
        username=account.pw_name,
        architecture=arch,
        distribution=release.get("PRETTY_NAME"),
        outputs=outputs,
        blockers=reason,
    )
    if data["inspect"] and data["section"] == "base":
        path = Path("/usr/share/i18n/SUPPORTED")
        result.update(
            previous_package_operation=historical_status(),
            timezones=sorted(available_timezones()),
            keyboards=keyboard_choices(),
            locales=sorted(
                {
                    line.split()[0]
                    for line in path.read_text().splitlines()
                    if re.fullmatch(r"\S+\.UTF-8 UTF-8", line)
                }
            )
            if path.exists()
            else [],
        )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
