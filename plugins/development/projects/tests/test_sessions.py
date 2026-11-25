"""Launch only a validated canonical project using the user service manager."""

import socket
from pathlib import Path

import pytest
from setup_plugin_projects import sessions

from setup_system_sdk import Outcome


@pytest.mark.parametrize(("action", "binary"), [("open-editor", "nvim"), ("open-codex", "codex")])
def test_reviewed_project_uses_exact_paths_and_static_argv(tmp_path, monkeypatch, action, binary):
    root = tmp_path / "project with spaces"
    root.mkdir()
    monkeypatch.setattr(sessions, "paths", lambda: {"owner/example": root})
    monkeypatch.setattr(sessions, "binary", lambda program: Path("/usr/bin") / program)
    terminal = tmp_path / "terminal"
    terminal.touch()
    monkeypatch.setattr(sessions, "TERMINAL", terminal)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    calls = []
    monkeypatch.setattr(
        sessions, "command", lambda *args, **kw: calls.append((args, kw)) or (0, "inactive")
    )
    monkeypatch.chdir(tmp_path)
    with socket.socket(socket.AF_UNIX) as display:
        display.bind("wayland-1")
        plan = sessions.session_plan(action, "owner/example")
        assert plan.outcome == Outcome.READY
        sessions.start(plan)
    argv = calls[-1][0]
    assert "--working-directory=" + str(root) in argv
    assert argv[argv.index("--") + 1 :] == (
        str(terminal),
        "/usr/bin/" + binary,
        *((".",) if binary == "nvim" else ()),
    )
    with pytest.raises(ValueError, match="canonical"):
        sessions.session_plan(action, "unknown/project")
