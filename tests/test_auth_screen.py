"""Both native authentication surfaces share geometry, typography and palette roles."""

from pathlib import Path

from native_workspace import render

from setup_system.domain.auth_screen import AuthScreen
from setup_system.infrastructure.login_plan import greeter_style
from setup_system.infrastructure.palette import palette

ROOT = Path(__file__).resolve().parents[1]


def test_native_login_and_lock_render_the_same_design() -> None:
    tokens = AuthScreen().tokens(palette(ROOT))
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
