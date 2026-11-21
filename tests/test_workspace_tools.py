"""Textual workspace actions keep exact project identity and never accept shell commands."""

from pathlib import Path

import pytest

from setup_system.application import tools
from setup_system.composition import build_tools
from setup_system.domain.tool_requests import TOOL_REQUEST, OpenProject, SimpleTool, answer_values


@pytest.mark.parametrize("name", ["unknown", "client"])
def test_editor_requires_an_exact_catalog_name(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setattr(tools, "projects", lambda root: {"pokerlost/pokerClient": Path("/fixture")})
    monkeypatch.setattr(
        "setup_system.application.tools.run_project",
        lambda *args, **kwargs: pytest.fail("Ambiguous project entered editor"),
    )
    with pytest.raises(ValueError, match="exact"):
        build_tools(Path("/source")).execute(
            OpenProject(action="open_project", name=name), confirmed=True
        )


def test_editor_uses_only_selected_working_directory_and_no_shell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tools, "projects", lambda root: {"pokerlost/pokerClient": tmp_path})
    calls = []
    monkeypatch.setattr(
        "setup_system.application.tools.run_project",
        lambda argv, **kwargs: calls.append((argv, kwargs)),
    )
    assert build_tools(tmp_path).execute(
        OpenProject(action="open_project", name="pokerlost/pokerClient"), confirmed=True
    ) == str(tmp_path)
    assert calls == [(["nvim", "."], {"cwd": tmp_path, "check": True})]


def test_catalog_error_does_not_launch_editor_or_expose_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail(root):
        raise ValueError("Invalid catalog")

    monkeypatch.setattr(tools, "projects", fail)
    monkeypatch.setattr(
        "setup_system.application.tools.run_project",
        lambda *args, **kwargs: pytest.fail("Invalid catalog reached process"),
    )
    with pytest.raises(ValueError, match="Invalid catalog"):
        build_tools(tmp_path).execute(SimpleTool(action="projects"))


@pytest.mark.parametrize(
    "fields",
    [
        {"action": "prepare_projects"},
        {"action": "open_project", "name": "repo"},
        {"action": "open_codex", "name": "repo"},
        {"action": "create_template", "name": "python", "destination": "workspace"},
        {"action": "update_template", "name": "python", "destination": "workspace"},
        {"action": "template_cache", "name": "python"},
        {"action": "restore", "backup_id": "20260101T123456Z-123456ab"},
        {"action": "shell"},
        {"action": "rtk"},
        {"action": "font_apply"},
        {"action": "git_use", "name": "profile", "destination": "workspace"},
        {"action": "git_configure", "name": "profile", "destination": "workspace"},
    ],
)
def test_all_mutating_tools_require_explicit_confirmation(tmp_path, fields) -> None:
    request = TOOL_REQUEST.validate_python(fields)
    with pytest.raises(ValueError, match="confirmation"):
        build_tools(tmp_path).execute(request)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "fields",
    [
        {"action": "id; reboot"},
        {"action": "create_template", "name": "python"},
        {"action": "restore", "backup_id": "../escape"},
        {"action": "open_codex", "name": "repo", "answers": {"irrelevant": True}},
        {"action": "git_inspect", "destination": "work", "name": "unexpected"},
        {"action": "shell", "destination": "inapplicable"},
        {
            "action": "create_template",
            "name": "python",
            "destination": "work",
            "answers": {"_src_path": "unreviewed"},
        },
    ],
)
def test_tool_contract_rejects_unknown_or_cross_action_fields(fields) -> None:
    with pytest.raises(ValueError):
        TOOL_REQUEST.validate_python(fields)


def test_copier_answers_are_immutable_deep_snapshots() -> None:
    source = {"nested": [{"choices": ["original"]}]}
    request = TOOL_REQUEST.validate_python(
        {"action": "create_template", "name": "python", "destination": "work", "answers": source}
    )
    source["nested"][0]["choices"].append("after inspection")
    dispatched = answer_values(request.answers)
    dispatched["nested"][0]["choices"].append("while running")
    assert answer_values(request.answers) == {"nested": [{"choices": ["original"]}]}
    assert TOOL_REQUEST.validate_json(TOOL_REQUEST.dump_json(request)) == request


def test_importing_views_does_not_load_native_adapters(tmp_path: Path) -> None:
    import subprocess
    import sys

    check = """
import sys
import setup_system.tui.installer
import setup_system.tui.tools
import setup_system.tui.displays
assert not [m for m in sys.modules if m.startswith("setup_system.infrastructure")]
"""
    subprocess.run(
        [sys.executable, "-I", "-B", "-c", check],
        cwd=tmp_path,
        env={"HOME": str(tmp_path / "not-created")},
        check=True,
    )
    assert not list(tmp_path.iterdir())
