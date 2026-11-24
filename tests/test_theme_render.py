"""Desktop settings must bind only the fields exposed by their templates."""

import json
import subprocess
from pathlib import Path

import pytest
from jinja2 import UndefinedError
from native_workspace import ROOT, files, render

ASSETS = Path(__file__).resolve().parents[1] / "assets/hyprland"


def test_theme_fields_survive_formatting_and_keep_unrelated_literals() -> None:
    source = (ASSETS / "profile.lua").read_text().replace("rounding = ", "rounding=")
    source += "\nlocal unrelated = { enabled = false }\n"
    actual = render(source, {"theme": {"rounding": 3, "effects": True}})
    assert "rounding=3," in actual
    assert "animations = { enabled = true }" in actual
    assert "unrelated = { enabled = false }" in actual


def test_panel_dimensions_and_scoped_rounding() -> None:
    theme = {"theme": {"rounding": 4, "compact_panel": True, "opacity": 0.83}}
    panel = json.loads(render((ASSETS / "waybar.json").read_text(), theme))
    assert panel["height"] == 30
    css = (ASSETS / "style.css").read_text() + "\n.fixed { border-radius: 17px; }"
    decorated = render(css, theme)
    assert "border-radius: 4px" in decorated and ".fixed { border-radius: 17px; }" in decorated
    assert "0.83)" in decorated and "border-radius: 9px" in decorated
    with pytest.raises(UndefinedError):
        render("@{ undefined_theme_key }@")


@pytest.mark.parametrize("layout", [None, "us", "us,ru", "fr,de"])
def test_desktop_applies_only_selected_keyboard_fields(layout, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for key in ("config", "data", "cache", "state"):
        monkeypatch.setenv("XDG_" + key.upper() + "_HOME", str(tmp_path / key))
    result, _ = files(ROOT, {"hyprland": True, "keyboard": layout})
    config = tmp_path / "config/hypr/setup-system"
    assert "kb_layout" not in result[config / "profile.lua"].decode()
    assert (config / "keyboard.lua" in result) == (layout is not None)
    if layout:
        actual = result[config / "keyboard.lua"].decode()
        assert f'kb_layout = "{layout}"' in actual
        assert ("kb_options" in actual) == ("," in layout)


def test_launcher_passes_quoted_xdg_paths_literally(tmp_path):
    owner = tmp_path / "developer's desk $USER"
    paths = {key: str(owner / key) for key in ("config", "data", "state", "cache")}
    exports = [
        line
        for line in (ASSETS / "start-hyprland").read_text().splitlines()
        if line.startswith("export XDG_") and "@{" in line
    ]
    assert len(exports) == 4
    script = render("\n".join(exports), info={"home": str(owner), "xdg": paths})
    for name, expected in paths.items():
        actual = subprocess.check_output(
            ["sh", "-c", script + f'\nprintf %s "$XDG_{name.upper()}_HOME"']
        )
        assert actual.decode() == str(expected)
