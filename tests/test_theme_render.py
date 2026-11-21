"""Desktop settings must bind only the fields exposed by their templates."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import UndefinedError

from setup_system.domain.settings import ThemeSettings
from setup_system.infrastructure.theme import render

ASSETS = Path(__file__).resolve().parents[1] / "assets/hyprland"


def test_theme_fields_survive_formatting_and_keep_unrelated_literals() -> None:
    source = (ASSETS / "profile.lua").read_text().replace("rounding = ", "rounding=")
    source += "\nlocal unrelated = { enabled = false }\n"
    actual = render(source, ThemeSettings(rounding=3, effects=True))
    assert "rounding=3," in actual
    assert "animations = { enabled = true }" in actual
    assert "unrelated = { enabled = false }" in actual


def test_panel_dimensions_and_scoped_rounding() -> None:
    theme = ThemeSettings(rounding=4, compact_panel=True, opacity=0.83)
    panel = json.loads(render((ASSETS / "waybar.json").read_text(), theme))
    assert panel["height"] == 30
    css = (ASSETS / "style.css").read_text() + "\n.fixed { border-radius: 17px; }"
    fonts = SimpleNamespace(family="Fixture Sans", emoji_fallback="Fixture Emoji", panel_size=11)
    decorated = render(css, theme, fonts=fonts)
    assert "border-radius: 4px" in decorated and ".fixed { border-radius: 17px; }" in decorated
    assert "0.83)" in decorated and "border-radius: 9px" in decorated
    with pytest.raises(UndefinedError):
        render("@{ undefined_theme_key }@")


@pytest.mark.parametrize("layout", [None, "us", "us,ru"])
def test_desktop_applies_only_selected_keyboard_fields(layout):
    profile = render((ASSETS / "profile.lua").read_text(), keyboard=layout)
    if layout is None:
        assert "kb_layout" not in profile
    else:
        assert f'kb_layout = "{layout}"' in profile
    assert ("kb_options" in profile) == (layout == "us,ru")


def test_launcher_passes_quoted_xdg_paths_literally(tmp_path):
    import subprocess

    from setup_system.domain.paths import XdgPaths
    from setup_system.infrastructure.theme import render_launcher

    owner = tmp_path / "developer's desk $USER"
    paths = XdgPaths(
        config=owner, data=owner / "data", state=owner / "state", cache=owner / "cache"
    )
    exports = [
        line
        for line in (ASSETS / "start-hyprland").read_text().splitlines()
        if line.startswith("export XDG_") and "@{" in line
    ]
    assert len(exports) == 4
    script = render_launcher("\n".join(exports), paths)
    for name, expected in paths.overrides().items():
        actual = subprocess.check_output(
            ["sh", "-c", script + f'\nprintf %s "$XDG_{name.upper()}_HOME"']
        )
        assert actual.decode() == str(expected)
