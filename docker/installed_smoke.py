"""Exercise installed Textual, offline help, native inspection and file recovery."""

import asyncio
import os
from pathlib import Path

from pydantic import JsonValue
from textual.widgets import Input

from setup_system.app import SetupApp
from setup_system.file_tasks import restore
from setup_system.infrastructure.managed_files import apply_files
from setup_system.runner import AsyncMakeRunner, Command, Target
from setup_system.ui.help import Help


async def exercise() -> None:
    config = Path(os.environ["XDG_CONFIG_HOME"]) / "setup-system"
    config.mkdir(parents=True, mode=0o700)
    prefs = config / "base.toml"
    prefs.write_text('schema_version=1\ntimezone="Europe/Paris"\n')
    prefs.chmod(0o600)
    app = SetupApp()
    async with app.run_test(size=(110, 45)) as pilot:
        await app.workers.wait_for_complete()
        assert app.query_one("#timezone", Input).value == "Europe/Paris"
        for shortcut, section in (("ctrl+2", "workspace"), ("ctrl+1", "base")):
            await pilot.press(shortcut)
            assert app.section == section
        await pilot.press("f1")
        assert isinstance(app.screen, Help)
        await pilot.press("escape")


async def main() -> None:
    home = Path.home()
    inspections: tuple[tuple[Target, dict[str, JsonValue]], ...] = (
        (Target.BASE_INSPECT, {"hyprland": True}),
        (
            Target.WORKSPACE_INSPECT,
            {"apps": ["neovim"], "languages": ["python"], "theme": {"rounding": 4}},
        ),
    )
    for target_id, selection in inspections:
        baseline: dict[str, JsonValue] = {"schema": 1, "settings": selection, "expected": None}
        facts = await AsyncMakeRunner().execute(Command.capture(target_id, baseline))
        assert facts.status in {"ready", "blocked"} and facts.observed.get("architecture"), facts
        assert facts.observed.get("section") == target_id.value.removesuffix("-inspect"), facts
    assert not any(home.iterdir()), "Installed Inspect modified the user HOME"
    await exercise()
    request: dict[str, JsonValue] = {
        "schema": 1,
        "settings": {"project": str(home)},
        "expected": None,
    }
    result = await AsyncMakeRunner().execute(Command.capture(Target.PROJECT_CHECK, request))
    assert result.status == "ready", result
    target = home / "config with spaces/example.ini"
    operation = apply_files({target: b"installed configuration"})
    assert target.read_bytes() == b"installed configuration"
    restore(operation.name, apply=True)
    assert not target.exists()


if __name__ == "__main__":
    asyncio.run(main())
