"""Persist validated, confirmed system intentions with a recoverable prior revision."""

import json
from pathlib import Path

from setup_system.domain.settings import SystemSettings
from setup_system.infrastructure.system_publication import observe_file, publish_file, save_backup

SETTINGS_PATH = Path("/etc/setup-system/system.toml")
BACKUPS = Path("/var/lib/setup-system/settings-backups")


def check_revision(expected: str | None) -> None:
    before = observe_file(SETTINGS_PATH)
    if (before.digest if before is not None else None) != expected:
        raise ValueError("System settings changed after plan confirmation")


def save(
    settings: SystemSettings,
    *,
    expected: str | None,
    path: Path | None = None,
    backups: Path = BACKUPS,
) -> None:
    path = SETTINGS_PATH if path is None else path
    settings = SystemSettings.model_validate(settings)
    before = observe_file(path)
    if (before.digest if before is not None else None) != expected:
        raise ValueError("System settings changed during apply; preserve the newer intent")
    values = settings.model_dump(mode="json", exclude_none=True)
    content = "".join(f"{key} = {json.dumps(value)}\n" for key, value in values.items()).encode()
    if before is not None and before.data != content:
        save_backup(backups / (before.digest + ".toml"), before)
    publish_file(path, content, before, mode=before.mode if before else 0o644, parent_mode=0o755)
