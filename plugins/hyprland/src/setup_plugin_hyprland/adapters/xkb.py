"""Read the Debian XKB layout catalog used by console and compositor settings."""

import re
from pathlib import Path

XKB = Path("/usr/share/X11/xkb")


def keyboard_layouts() -> list[str]:
    rules = XKB / "rules/base.lst"
    if not rules.is_file():
        return []
    layouts: set[str] = set()
    selected = False
    for line in rules.read_text().splitlines():
        if line.startswith("!"):
            selected = line.split() == ["!", "layout"]
        elif selected and line.strip():
            name = line.split()[0]
            if re.fullmatch(r"[a-z][a-z0-9_-]{0,15}", name) and (XKB / "symbols" / name).is_file():
                layouts.add(name)
    return sorted(layouts)


def validate_layouts(layouts: list[str]) -> None:
    if not (
        1 <= len(layouts) <= 4
        and len(layouts) == len(set(layouts))
        and set(layouts).issubset(keyboard_layouts())
    ):
        raise ValueError("Select one to four distinct installed XKB layouts.")
