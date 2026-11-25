"""Exercise installed Textual, offline help, native inspection and file recovery."""

import asyncio
from pathlib import Path

from pydantic import JsonValue

from setup_system.app import SetupApp
from setup_system.file_tasks import restore
from setup_system.infrastructure.managed_files import apply_files
from setup_system.runner import AsyncMakeRunner, Command, Target
from setup_system.ui.help import Help


async def exercise() -> None:
    app = SetupApp()
    async with app.run_test(size=(110, 45)) as pilot:
        for shortcut, section in (("ctrl+2", "workspace"), ("ctrl+1", "base")):
            await pilot.press(shortcut)
            assert app.section == section
        await pilot.press("f1")
        assert isinstance(app.screen, Help)
        await pilot.press("escape")


asyncio.run(exercise())
home = Path.home()
baseline: dict[str, JsonValue] = {"schema": 1, "settings": {}, "expected": None}
facts = asyncio.run(AsyncMakeRunner().execute(Command.capture(Target.BASE_INSPECT, baseline)))
assert facts.status in {"ready", "blocked"} and facts.observed.get("architecture"), facts
assert not any(home.iterdir()), "Installed Inspect modified the user HOME"
request: dict[str, JsonValue] = {"schema": 1, "settings": {"project": str(home)}, "expected": None}
result = asyncio.run(AsyncMakeRunner().execute(Command.capture(Target.PROJECT_CHECK, request)))
assert result.status == "ready", result
target = home / "config with spaces/example.ini"
operation = apply_files({target: b"installed configuration"})
assert target.read_bytes() == b"installed configuration"
restore(operation.name, apply=True)
assert not target.exists()
