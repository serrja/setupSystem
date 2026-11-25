"""Exercise user publication, rollback, exact capabilities and shared desktop styling."""

import asyncio
import hashlib
import json
import subprocess

import pytest
from setup_plugin_foot import Plugin as Foot
from setup_plugin_greeter.login_plan import greeter_style
from setup_plugin_hyprland import Display, Plugin, Settings, displays
from setup_plugin_hyprland.models import DisplayOutput, MonitorLayout
from setup_plugin_hyprlock import Plugin as Hyprlock
from setup_plugin_waybar import Plugin as Waybar

from setup_system.layout import resource_root
from setup_system_sdk import Inputs, Mutation, Outcome, ownership_context


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "user's space $USER (Й)"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(root))
    for name in ("CONFIG", "DATA", "STATE", "CACHE"):
        monkeypatch.setenv("XDG_" + name + "_HOME", str(root / name.lower()))
    return root


def test_user_profile_is_read_only_until_apply_and_preserves_edits(home):
    backend = Plugin()
    settings = Settings(effects=True, rounding=3, keyboard="us,ru")
    initial = backend.prepare("configure", settings, Inputs())
    assert initial.outcome == Outcome.READY and not any(home.iterdir())
    mutation = Mutation(
        action_id="configure", inputs={}, settings=settings.model_dump(), plan=initial
    )
    result = asyncio.run(backend.apply_request("configure", mutation))
    assert result.outcome == Outcome.CHANGED
    assert asyncio.run(backend.verify(initial, settings, Inputs())).outcome == Outcome.COMPLETE
    config = home / "config/hypr/setup-system"
    assert "rounding = 3" in (config / "profile.lua").read_text()
    assert 'kb_layout = "us,ru"' in (config / "keyboard.lua").read_text()
    assert "kb_options" in (config / "keyboard.lua").read_text()
    launcher = home / "data/setup-system/bin/start-hyprland"
    assert launcher.stat().st_mode & 0o100
    script = "\n".join(s for s in launcher.read_text().splitlines() if s.startswith("export XDG_"))
    for key in ("config", "data", "state", "cache"):
        value = subprocess.check_output(
            ["sh", "-c", script + f'\nprintf %s "$XDG_{key.upper()}_HOME"']
        )
        assert value.decode() == str(home / key)
    with ownership_context(tuple(result.resources)):
        assert backend.prepare("configure", settings, Inputs()).outcome == Outcome.NO_CHANGES
        assert backend.prepare("configure", Settings(rounding=7), Inputs()).outcome == Outcome.READY
        old_mode = (config / "profile.lua").stat().st_mode & 0o777
        (config / "profile.lua").chmod(0o640)
        assert backend.prepare("configure", settings, Inputs()).outcome == Outcome.BLOCKED
        (config / "profile.lua").chmod(old_mode)
        (config / "profile.lua").write_text("-- local edit")
        assert backend.prepare("configure", settings, Inputs()).outcome == Outcome.BLOCKED
        assert (config / "profile.lua").read_text() == "-- local edit"
        (config / "profile.lua").unlink()
        assert backend.prepare("configure", settings, Inputs()).outcome == Outcome.BLOCKED


def test_unknown_existing_entrypoint_and_symlink_are_preserved(home):
    target = home / "config/hypr/hyprland.lua"
    target.parent.mkdir(parents=True)
    target.write_text("-- local")
    assert Plugin().prepare("configure", Settings(), Inputs()).outcome == Outcome.BLOCKED
    target.unlink()
    target.symlink_to(home / "another")
    with pytest.raises(ValueError):
        Plugin().prepare("configure", Settings(), Inputs())


def test_template_settings_and_keyboard_metadata(home, monkeypatch):
    from setup_system_sdk import managed_assets

    waybar = Waybar()
    configured = waybar.files(
        "configure", waybar.settings_model(compact=True, opacity=0.83, rounding=4), Inputs()
    )
    panel = json.loads(
        next(data for path, data in configured.items() if path.name == "waybar.json")
    )
    assert panel["position"] == "top" and panel["height"] == 30
    css = next(data for path, data in configured.items() if path.name == "style.css")
    assert b"border-radius: 4px" in css and b"0.83)" in css
    root = resource_root()
    styles = (root / "config/shared/desktop.toml").read_text()
    fake = home / "palette"
    (fake / "config/shared").mkdir(parents=True)
    (fake / "config/shared/desktop.toml").write_text(
        styles.replace("input_width = 384", "input_width = 512")
    )
    (fake / "config/shared/palette.toml").write_bytes(
        (root / "config/shared/palette.toml").read_bytes()
    )
    monkeypatch.setattr(managed_assets, "resource_root", lambda: fake)
    lock = Hyprlock()
    rendered = next(iter(lock.files("configure", lock.settings_model(), Inputs()).values()))
    assert b"size = 512," in rendered and b"$USER" in rendered
    assert b"min-width: 512px" in greeter_style(fake)
    foot = Foot()
    data = next(iter(foot.files("configure", foot.settings_model(font_size=14), Inputs()).values()))
    assert b"size=14" in data


def test_stale_monitor_choice_refuses_persistent_config(home, monkeypatch):
    layout = MonitorLayout(output="DP-1", mode="1920x1080@60", x=0, y=0, scale=1.0)
    discovered = (DisplayOutput(current=layout, modes=(layout.mode,)),)
    monkeypatch.setattr(displays, "discover_outputs", lambda: discovered)
    plugin = Plugin()
    selected = Display.model_validate(layout.model_dump())
    initial = plugin.prepare("save-monitor", Settings(), selected)
    assert initial.resources[0].after == hashlib.sha256((layout.rule() + "\n").encode()).hexdigest()
    discovered = ()
    with pytest.raises(ValueError, match="no longer available"):
        plugin.prepare("save-monitor", Settings(), selected)
    assert not (home / "config").exists()


def test_each_renderer_owns_disjoint_files(home):
    rows = []
    for backend in (Plugin(), Hyprlock(), Waybar(), Foot()):
        rows += list(backend.files("configure", backend.settings_model(), Inputs()))
    assert len(set(rows)) == len(rows)
