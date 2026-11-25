"""Both native authentication surfaces share geometry, typography and palette roles."""

from pathlib import Path

from native_workspace import render

from setup_system.infrastructure.login_plan import greeter_style
from setup_system.infrastructure.palette import desktop_style, palette

ROOT = Path(__file__).resolve().parents[1]


def test_native_login_and_lock_render_the_same_design() -> None:
    tokens = desktop_style(ROOT).auth_screen.tokens(palette(ROOT))
    login = greeter_style(ROOT).decode()
    lock = render((ROOT / "assets/hyprland/hyprlock.conf").read_text())
    assert str(tokens["font"]) in login and str(tokens["font"]) in lock
    for role in ("background", "card", "accent", "input", "error", "foreground"):
        color = str(tokens[role])
        assert color in login and color[1:] in lock
    assert f"min-width: {tokens['input_width']}px" in login
    assert f"size = {tokens['input_width']}, {tokens['input_height']}" in lock
    assert f"border-radius: {tokens['radius']}px" in login
    assert f"rounding = {tokens['radius']}" in lock
    assert "$USER" in lock and "exec " not in lock


def test_packaged_geometry_reaches_independent_renderers(tmp_path, monkeypatch):
    import shutil

    import native_workspace

    shutil.copytree(ROOT / "config", tmp_path / "config")
    styles = tmp_path / "config/shared/desktop.toml"
    styles.write_text(styles.read_text().replace("input_width = 384", "input_width = 512"))
    (tmp_path / "assets/login").mkdir(parents=True)
    shutil.copy(ROOT / "assets/login/greeter.css", tmp_path / "assets/login")
    shutil.copytree(ROOT / "ansible/tasks", tmp_path / "ansible/tasks")
    monkeypatch.setattr(native_workspace, "ROOT", tmp_path)
    assert b"min-width: 512px" in greeter_style(tmp_path)
    assert "size = 512," in render((ROOT / "assets/hyprland/hyprlock.conf").read_text())
