"""Catalog/picker checks never require a selected project's language runtime."""

from __future__ import annotations

import subprocess
from io import StringIO
from pathlib import Path

import pytest
from typer.testing import CliRunner

from setup_system.application.project_layout import active_projects, load_layout
from setup_system.cli import main

CATALOG = """schemaVersion = 1
projectsRoot = "$HOME/Projects"
ownerGroups = ["pokerlost"]
[[project]]
id = "pokerlost/pokerClient"
state = "existing"
"""


def catalog(tmp_path: Path, text: str = CATALOG) -> tuple[Path, Path]:
    root = tmp_path / "catalog"
    (root / "manifests").mkdir(parents=True)
    (root / "manifests/projects.toml").write_text(text)
    home = tmp_path / "home"
    home.mkdir()
    return root, home


@pytest.mark.parametrize(
    ("before", "after", "message"),
    [
        ("schemaVersion = 1", "schemaVersion = 2", "schema"),
        ("$HOME/Projects", "/tmp/Projects", "literal"),
        ("$HOME/Projects", "$HOME/../Projects", "unsafe"),
        ("$HOME/Projects", "$HOME/Projects/", "unsafe"),
        ('["pokerlost"]', "[]", "unique"),
        ('["pokerlost"]', '["pokerlost", "pokerlost"]', "unique"),
        ('["pokerlost"]', '["bad-owner"]', "owner group"),
        ("pokerlost/pokerClient", "pokerlost/../pokerClient", "project ID"),
        ("pokerlost/pokerClient", "other/pokerClient", "project ID"),
        ('state = "existing"', 'state = "invented"', "project state"),
        (
            'state = "existing"',
            'state = "existing"\nlegacyPath = "$HOME/Projects/pokerlost/pokerClient"',
            "identical",
        ),
    ],
)
def test_invalid_catalog_is_rejected_without_creating_checkouts(
    tmp_path: Path, before: str, after: str, message: str
) -> None:
    root, home = catalog(tmp_path, CATALOG.replace(before, after))
    with pytest.raises(ValueError, match=message):
        load_layout(root, home=home)
    assert list(home.iterdir()) == []


@pytest.mark.parametrize(
    ("record", "message"),
    [
        ('id = "pokerlost/pokerClient"\nstate = "existing"', "repeated"),
        (
            'id = "pokerlost/other"\nstate = "existing"\n'
            'legacyPath = "$HOME/Projects/pokerlost/pokerClient"',
            "overlap",
        ),
    ],
)
def test_duplicate_id_or_path_is_not_a_second_checkout(
    tmp_path: Path, record: str, message: str
) -> None:
    root, home = catalog(tmp_path, CATALOG + "\n[[project]]\n" + record + "\n")
    with pytest.raises(ValueError, match=message):
        load_layout(root, home=home)
    assert not (home / "Projects").exists()


def test_picker_resolves_a_dart_only_client_without_a_python_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, home = catalog(tmp_path)
    client = home / "Projects/pokerlost/pokerClient"
    client.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(client)], check=True, capture_output=True)
    (client / "pubspec.yaml").write_text("name: synthetic_client\npublish_to: none\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(main, "ROOT", root)
    before = (client / ".git/config").read_bytes()
    index = client / ".git/index"
    assert not index.exists()
    runner = CliRunner()
    listed = runner.invoke(main.app, ["projects", "list"])
    selected = runner.invoke(main.app, ["projects", "path", "--name", "pokerlost/pokerClient"])
    assert listed.exit_code == selected.exit_code == 0
    assert listed.stdout.strip() == f"pokerlost/pokerClient: {client}"
    assert selected.stdout.strip() == str(client)
    assert (client / ".git/config").read_bytes() == before
    assert not index.exists()
    assert not list(client.glob("*.py")) and not (client / ".venv").exists()
    assert not (home / "Projects/serrja/pythonTemplate").exists()
    templates = runner.invoke(main.app, ["templates", "list"])
    assert templates.exit_code == 0
    assert "pokerClient" not in templates.stdout


def test_picker_rejects_parent_git_and_never_initializes_a_child(tmp_path: Path) -> None:
    root, home = catalog(tmp_path)
    group = home / "Projects/pokerlost"
    client = group / "pokerClient"
    client.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(group)], check=True, capture_output=True)
    before = (group / ".git/config").read_bytes()
    assert active_projects(load_layout(root, home=home)) == {}
    assert not (client / ".git").exists()
    assert (group / ".git/config").read_bytes() == before


@pytest.mark.parametrize("name", ["pokerClient", "pokerlost/missing", "../pokerClient"])
def test_picker_rejects_ambiguous_or_unknown_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setattr(main, "_discover_projects", lambda: {"pokerlost/pokerClient": tmp_path})
    result = CliRunner().invoke(main.app, ["projects", "path", "--name", name])
    assert result.exit_code == 2
    assert "exact name" in result.output


@pytest.mark.parametrize("selector", ["fzf", "prompt"])
@pytest.mark.parametrize("selection", ["pokerlost/pokerClient", "", "unknown"])
def test_interactive_picker_handles_selection_cancellation_and_unknown_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selector: str, selection: str
) -> None:
    stdin = StringIO(selection + "\n")
    monkeypatch.setattr(stdin, "isatty", lambda: True)
    monkeypatch.setattr(main.sys, "stdin", stdin)
    monkeypatch.setattr(
        main.shutil, "which", lambda name: "/fixture/fzf" if selector == "fzf" else None
    )
    calls: list[list[str]] = []

    def run(args: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert options["input"] == "pokerlost/pokerClient\n"
        return subprocess.CompletedProcess(args, 0 if selection else 130, selection + "\n")

    monkeypatch.setattr(main.subprocess, "run", run)
    projects = {"pokerlost/pokerClient": tmp_path}
    if selection == "unknown":
        with pytest.raises(main.typer.BadParameter, match="exact name"):
            main._choose_project(projects, None)
    else:
        assert main._choose_project(projects, None) == (tmp_path if selection else None)
    assert bool(calls) == (selector == "fzf")


def test_noninteractive_picker_requires_an_explicit_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main.sys, "stdin", StringIO(""))
    with pytest.raises(main.typer.BadParameter, match="--name"):
        main._choose_project({}, None)


@pytest.mark.parametrize("command", ["list", "path", "open"])
def test_catalog_errors_reach_the_cli_without_launching_a_project(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    def invalid() -> dict[str, Path]:
        raise ValueError("Two active checkouts require review")

    monkeypatch.setattr(main, "_discover_projects", invalid)
    result = CliRunner().invoke(main.app, ["projects", command])
    assert result.exit_code == 2
    assert "Two active checkouts" in result.output


@pytest.mark.parametrize("existing_session", [False, True])
def test_project_open_uses_only_the_editor_and_exact_selected_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing_session: bool
) -> None:
    editor = tmp_path / ".local/bin/nvim"
    editor.parent.mkdir(parents=True)
    editor.touch()
    client = tmp_path / "Projects/pokerlost/pokerClient"
    client.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(main, "_discover_projects", lambda: {"pokerlost/pokerClient": client})
    monkeypatch.setattr(main.shutil, "which", lambda name: "/fixture/tmux")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(args: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, options))
        status = 0 if existing_session or "has-session" not in args else 1
        return subprocess.CompletedProcess(args, status)

    monkeypatch.setattr(main.subprocess, "run", run)
    runner = CliRunner()
    plain = runner.invoke(main.app, ["projects", "open", "--name", "pokerlost/pokerClient"])
    assert plain.exit_code == 0
    assert calls == [([str(editor)], {"cwd": client, "check": False})]
    calls.clear()
    tmux = runner.invoke(
        main.app, ["projects", "open", "--name", "pokerlost/pokerClient", "--tmux"]
    )
    assert tmux.exit_code == 0
    assert [args[1] for args, _ in calls] == (
        ["has-session", "attach-session"]
        if existing_session
        else ["has-session", "new-session", "new-window", "attach-session"]
    )
    for args, _ in calls:
        if "-c" in args:
            assert args[args.index("-c") + 1] == str(client)
        assert not any(name in args for name in ["python", "uv", "pokerClient"])
