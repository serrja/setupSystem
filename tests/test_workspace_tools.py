"""Project commands stay finite, user-scoped and confirmed."""

import os
from pathlib import Path

import pytest

from setup_system import project_tasks
from setup_system.domain.templates import answer_values, snapshot_answers


@pytest.mark.parametrize("name", ["unknown", "client", "../escape"])
def test_editor_requires_an_exact_catalog_name(monkeypatch, tmp_path: Path, name: str) -> None:
    monkeypatch.setattr(project_tasks, "paths", lambda root: {"pokerlost/pokerClient": tmp_path})
    monkeypatch.setattr(
        project_tasks.subprocess, "run", lambda *a, **kw: pytest.fail("Launched an unknown project")
    )
    with pytest.raises(ValueError, match="exact"):
        project_tasks.execute(
            tmp_path,
            "project-editor",
            {"schema": 1, "expected": None, "settings": {"project_id": name}},
        )


@pytest.mark.parametrize(
    ("target", "program"), [("project-editor", "nvim"), ("project-codex", "codex")]
)
def test_projects_launch_in_exact_root_without_shell(
    monkeypatch, tmp_path, target, program
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(project_tasks, "paths", lambda root: {"serrja/pythonTemplate": tmp_path})
    monkeypatch.setattr(project_tasks.shutil, "which", lambda name: "/usr/bin/" + name)
    calls = []
    monkeypatch.setattr(
        project_tasks.subprocess, "run", lambda argv, **kw: calls.append((argv, kw))
    )
    project_tasks.execute(
        tmp_path,
        target,
        {"schema": 1, "expected": None, "settings": {"project_id": "serrja/pythonTemplate"}},
    )
    assert calls == [
        (
            ["/usr/bin/" + program] + (["."] if program == "nvim" else []),
            {"cwd": tmp_path, "check": True},
        )
    ]


@pytest.mark.parametrize(
    "target",
    [
        "project-prepare",
        "template-cache",
        "template-create",
        "template-update",
        "git-configure",
        "git-use",
        "shell-configure",
        "rtk-install",
    ],
)
def test_mutations_require_a_confirmed_preview(tmp_path, target) -> None:
    with pytest.raises(ValueError):
        project_tasks.execute(
            tmp_path, target + "-apply", {"schema": 1, "expected": None, "settings": {}}
        )


def test_bad_choice_and_stale_checkout_do_not_launch(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        project_tasks, "paths", lambda root: (_ for _ in ()).throw(ValueError("Catalog error"))
    )
    monkeypatch.setattr(
        project_tasks.subprocess, "run", lambda *a, **kw: pytest.fail("Launched editor")
    )
    with pytest.raises(ValueError, match="Catalog error"):
        project_tasks.execute(
            tmp_path,
            "project-editor",
            {"schema": 1, "expected": None, "settings": {"project_id": "serrja/setupSystem"}},
        )
    with pytest.raises(ValueError):
        project_tasks.execute(
            tmp_path,
            "project-editor",
            {"schema": 1, "expected": None, "settings": {"project_id": "x", "answers": {"a": 1}}},
        )
    with pytest.raises(ValueError):
        project_tasks.execute(
            tmp_path, "shell-command", {"schema": 1, "expected": None, "settings": {}}
        )


def test_copier_answers_are_deep_snapshots() -> None:
    initial = {"nested": [{"choices": ["original"]}]}
    snapshot = snapshot_answers(initial)
    initial["nested"][0]["choices"].append("changed")
    answer_values(snapshot)["nested"].append("changed")
    assert answer_values(snapshot) == {"nested": [{"choices": ["original"]}]}
    with pytest.raises(ValueError, match="Internal"):
        snapshot_answers({"_src_path": "other"})


def test_importing_ui_does_not_load_project_executors(tmp_path) -> None:
    import subprocess
    import sys

    check = (
        "import sys; import setup_system.app; "
        "assert 'setup_system.project_tasks' not in sys.modules"
    )
    subprocess.run(
        [sys.executable, "-I", "-B", "-c", check],
        cwd=tmp_path,
        env={**os.environ, "HOME": str(tmp_path)},
        check=True,
    )
    assert not list(tmp_path.iterdir())


def test_empty_xdg_exports_from_make_use_home_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    for key in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(key, "")
    root = Path(__file__).resolve().parents[1]
    result = project_tasks.execute(
        root, "project-list", {"schema": 1, "expected": None, "settings": {}}
    )
    assert result.status == "ready" and "python" in result.observed["templates"]
    assert result.observed["git_profiles"] == {}
    assert list(tmp_path.iterdir()) == []
