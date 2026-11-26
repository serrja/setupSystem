"""Render a named desktop entry for a packaged application."""

import re
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class LauncherFields(BaseModel):
    profile_id: str = Field(
        pattern=r"^[a-z][a-z0-9_-]{0,47}$",
        title="Launcher profile ID",
        description="Stable identifier used in the desktop entry filename.",
    )
    name: str = Field(
        min_length=1,
        max_length=80,
        title="Application menu label",
        description="Display name shown by the desktop application menu.",
    )

    @field_validator("name")
    @classmethod
    def menu_label(cls, value: str) -> str:
        if not value.isprintable() or value.strip() != value or "\\" in value:
            raise ValueError(
                "Enter a single-line menu label without surrounding whitespace or escapes"
            )
        return value


def launcher_file(
    directory: Path, profile: LauncherFields, plugin: str, executable: str, args: tuple[str, ...]
) -> dict[Path, bytes]:
    if not re.fullmatch(r"[a-z][a-z0-9-]*", plugin) or not re.fullmatch(
        r"/usr/bin/[a-z][a-z0-9-]*", executable
    ):
        raise ValueError("Expected a packaged application and launcher namespace")
    if not all(re.fullmatch(r"(?:-{1,2})?[a-z][a-z0-9-]{0,63}", arg) for arg in args):
        raise ValueError("The application launcher accepts only fixed native argument words")
    path = directory / f"setup-system-{plugin}-{profile.profile_id}.desktop"
    content = (
        "[Desktop Entry]\nType=Application\n"
        f"Name={profile.name}\nTryExec={executable}\n"
        f"Exec={' '.join((executable, *args))}\n"
        f"Icon={Path(executable).name}\nTerminal=false\n"
    )
    return {path: content.encode()}
