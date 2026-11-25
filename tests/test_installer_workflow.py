"""Direct delivery and optional application selection replace the temporary proxy flow."""

import asyncio
import json
import runpy
import tomllib
from pathlib import Path

from test_native_ui import Tasks, app_for, idle

from setup_system.models import BaseOptions, WorkspaceOptions
from setup_system.runner import Target

ROOT = Path(__file__).resolve().parents[1]


def test_happ_is_not_required_for_base_or_theme_plan() -> None:
    native = runpy.run_path(str(ROOT / "ansible/filter_plugins/native.py"))
    base = tomllib.loads((ROOT / "config/base/packages.toml").read_text())
    apps = tomllib.loads((ROOT / "config/workspace/applications.toml").read_text())
    for settings in (
        BaseOptions(hyprland=True),
        WorkspaceOptions(desktop_theme=True),
        WorkspaceOptions(),
    ):
        values = settings.model_dump(mode="json")
        assert "happ" not in values.get("apps", ())
        assert "happ" not in native["package_names"](
            settings.model_dump_json(), json.dumps(base), json.dumps(apps)
        )
    assert (
        native["package_names"](
            WorkspaceOptions().model_dump_json(),
            json.dumps(base),
            json.dumps(apps),
        )
        == []
    )


def test_happ_is_selected_only_by_explicit_workspace_intention() -> None:
    apps = tomllib.loads((ROOT / "config/workspace/applications.toml").read_text())
    validate = runpy.run_path(str(ROOT / "ansible/filter_plugins/native.py"))["validate_request"]
    for selected in ([], ["happ"]):
        plan = validate(
            json.dumps({"schema": 1, "settings": {"apps": selected}, "expected": None}),
            "workspace",
            json.dumps(apps),
            "{}",
        )
        assert ("happ" in plan["apps"]) == bool(selected)
        assert "subscription" not in plan
    assert apps["happ"]["github"] == "Happ-proxy/happ-desktop"


def test_cancelled_confirmation_never_executes(tmp_path) -> None:
    async def exercise():
        tasks = Tasks()
        app = app_for(tmp_path, tasks)
        async with app.run_test(size=(100, 35)) as pilot:
            await pilot.press("ctrl+2")
            await pilot.click("#plan")
            await idle(app, pilot)
            await pilot.click("#apply")
            await pilot.press("escape")
            assert [target for target, _, _ in tasks.calls] == [Target.WORKSPACE_PLAN]
            assert not app.settings.directory.exists()

    asyncio.run(exercise())
