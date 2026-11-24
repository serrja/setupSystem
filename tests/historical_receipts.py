"""Published v0.4 backup fixtures for migration and recovery tests."""

import json
from pathlib import Path


def historical_backup(paths: list[Path]) -> Path:
    """Construct the published v0.4 manifest representation for migration tests."""
    import hashlib
    import shutil
    import stat
    import uuid
    from datetime import UTC, datetime

    from setup_system.infrastructure.user_storage import inside_home, state_root

    base = inside_home(state_root() / "backups")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    folder = base / run_id
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    folder.chmod(0o700)
    home = Path.home().resolve()
    items: list[dict[str, str | int]] = []
    for source in sorted(set(paths)):
        target = inside_home(source)
        if not target.exists():
            continue
        if not target.is_file():
            raise ValueError(f"Backup source is not a regular file: {target}")
        relative = target.relative_to(home)
        backup = folder / "files" / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)
        backup.chmod(0o600)
        items.append(
            {
                "source": str(target),
                "home_relative": str(relative),
                "backup": str(backup.relative_to(folder)),
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "mode": stat.S_IMODE(target.stat().st_mode),
            }
        )
    manifest = folder / "manifest.json"
    manifest.write_text(
        json.dumps({"run_id": run_id, "items": items, "moves": []}, ensure_ascii=False, indent=2)
        + "\n"
    )
    manifest.chmod(0o600)
    return folder
