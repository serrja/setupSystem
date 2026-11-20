"""Textual workspace actions keep exact project identity and never accept shell commands."""

from pathlib import Path

import pytest

from setup_system.application import tools
from setup_system.application.tools import ToolRequest


@pytest.mark.parametrize("name", ["unknown", "client"])
def test_editor_requires_an_exact_catalog_name(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setattr(tools, "projects", lambda root: {"pokerlost/pokerClient": Path("/fixture")})
    monkeypatch.setattr(
        "subprocess.run", lambda *args, **kwargs: pytest.fail("Ambiguous project entered editor")
    )
    with pytest.raises(ValueError, match="exact"):
        tools.execute(Path("/source"), ToolRequest("open_project", name=name), confirmed=True)


def test_editor_uses_only_selected_working_directory_and_no_shell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tools, "projects", lambda root: {"pokerlost/pokerClient": tmp_path})
    calls = []
    monkeypatch.setattr("subprocess.run", lambda argv, **kwargs: calls.append((argv, kwargs)))
    assert tools.execute(
        tmp_path, ToolRequest("open_project", name="pokerlost/pokerClient"), confirmed=True
    ) == str(tmp_path)
    assert calls == [(["nvim", "."], {"cwd": tmp_path, "check": True})]


def test_catalog_error_does_not_launch_editor_or_expose_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail(root):
        raise ValueError("Invalid catalog")

    monkeypatch.setattr(tools, "projects", fail)
    monkeypatch.setattr(
        "subprocess.run", lambda *args, **kwargs: pytest.fail("Invalid catalog reached process")
    )
    with pytest.raises(ValueError, match="Invalid catalog"):
        tools.execute(tmp_path, ToolRequest("projects"))


def test_all_mutating_tools_require_explicit_confirmation(tmp_path: Path) -> None:
    for action in tools.MUTATING_TOOLS:
        with pytest.raises(ValueError, match="confirmation"):
            tools.execute(tmp_path, ToolRequest(action))
    assert not list(tmp_path.iterdir())


def test_unknown_workspace_action_is_not_a_shell_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown"):
        tools.execute(tmp_path, ToolRequest("id; reboot"), confirmed=True)
