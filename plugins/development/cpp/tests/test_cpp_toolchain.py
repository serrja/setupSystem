"""Keep clangd user configuration intact across repeated settings actions."""

from pathlib import Path

import pytest
from setup_plugin_cpp import Plugin, Settings

from setup_system_sdk import Inputs


def test_index_is_a_managed_document_and_foreign_yaml_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    path = Path(tmp_path / "cfg/clangd/config.yaml")
    path.parent.mkdir(parents=True)
    path.write_text("# existing options\nCompileFlags: {Add: [-Wall]}\n")
    path.chmod(0o600)
    plugin = Plugin()
    planned = plugin.files("configure", Settings(background="Skip"), Inputs())[path]
    assert b"CompileFlags: {Add: [-Wall]}" in planned and b"Background: Skip" in planned
    path.write_bytes(planned)
    assert plugin.files("configure", Settings(background="Skip"), Inputs())[path] == planned
    path.write_text("# >>> setupSystem >>>\n")
    with pytest.raises(ValueError):
        plugin.files("configure", Settings(), Inputs())
