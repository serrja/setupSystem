"""Catalog/picker checks never require a selected project's language runtime."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

from setup_system.domain.projects import ProjectCatalog, ProjectRecord
from setup_system.infrastructure.project_layout import active_projects, load_layout

CATALOG = """schemaVersion = 1
projectsRoot = "$HOME/Projects"
ownerGroups = ["pokerlost"]
[[project]]
id = "pokerlost/pokerClient"
state = "existing"
"""


def test_project_rules_remains_active_with_reviewed_immutable_provenance() -> None:
    root = Path(__file__).resolve().parents[1]
    data = ProjectCatalog.model_validate(
        tomllib.loads((root / "manifests/projects.toml").read_text())
    )
    record = next(project for project in data.project if project.id == "pokerlost/projectRules")
    assert record.origin == "git@github.com:pokerlost/projectRules.git"
    assert record.state == "existing" and record.policy is not None
    assert record.policy.commit == "537da92f8e22f35355a96f868d1b98d83e066c75"
    assert record.policy.document == "docs/project.md"
    assert (
        record.policy.sha256 == "be4a92f6ac9c546be816b12652dc7fff4113dd049065613a7b3c026deb8b8dee"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"commit": "main"},
        {"document": "../policy.md"},
        {"document": "/policy.md"},
        {"sha256": "bad"},
    ],
)
def test_policy_metadata_rejects_mutable_or_unsafe_provenance(change: dict[str, str]) -> None:
    from pydantic import ValidationError

    policy = {"commit": "a" * 40, "document": "docs/project.md", "sha256": "b" * 64, **change}
    with pytest.raises(ValidationError):
        ProjectRecord(
            id="pokerlost/projectRules",
            state="existing",
            origin="git@github.com:pokerlost/projectRules.git",
            policy=policy,
        )


def test_explicit_project_root_with_spaces_verifies_all_effective_origins(tmp_path: Path) -> None:
    text = CATALOG.replace("$HOME/Projects", "$HOME/Workspace with spaces")
    text += 'origin = "git@github.com:pokerlost/pokerClient.git"\n'
    root, home = catalog(tmp_path, text)
    layout = load_layout(root, home=home)
    client = home / "Workspace with spaces/pokerlost/pokerClient"
    client.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(client)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(client),
            "remote",
            "add",
            "origin",
            "git@github.com:pokerlost/pokerClient.git",
        ],
        check=True,
    )
    assert active_projects(layout) == {"pokerlost/pokerClient": client}
    subprocess.run(
        [
            "git",
            "-C",
            str(client),
            "config",
            "--add",
            "remote.origin.pushurl",
            "https://github.com/other/pokerClient.git",
        ],
        check=True,
    )
    with pytest.raises(ValueError, match="canonical fetch/push origins"):
        active_projects(layout)
    subprocess.run(
        ["git", "-C", str(client), "config", "--unset", "remote.origin.pushurl"], check=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(client),
            "config",
            "url.https://github.com/other/.insteadOf",
            "git@github.com:pokerlost/",
        ],
        check=True,
    )
    with pytest.raises(ValueError, match="canonical fetch/push origins"):
        active_projects(layout)
    assert not (home / "Projects/serjja").exists()


def test_policy_origin_cannot_name_another_repository() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="canonical GitHub"):
        ProjectRecord(
            id="pokerlost/projectRules",
            state="existing",
            origin="https://github.com/other/projectRules.git",
            policy={"commit": "a" * 40, "document": "docs/project.md", "sha256": "b" * 64},
        )


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
        ("schemaVersion = 1", "schemaVersion = true", "schema"),
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
            "schema",
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
            "schema",
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


def test_catalog_structure_errors_do_not_expose_input(tmp_path: Path) -> None:
    text = CATALOG + '\nreviewOwner = "SYNTHETIC_PRIVATE_INPUT"\n'
    root, home = catalog(tmp_path, text)
    with pytest.raises(ValueError, match="schema") as failure:
        load_layout(root, home=home)
    assert "SYNTHETIC_PRIVATE_INPUT" not in str(failure.value)
    assert not list(home.iterdir())


def test_picker_resolves_a_dart_only_client_without_a_python_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from setup_system.composition import build_tools
    from setup_system.domain.tool_requests import SimpleTool

    root, home = catalog(tmp_path)
    client = home / "Projects/pokerlost/pokerClient"
    client.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(client)], check=True, capture_output=True)
    (client / "pubspec.yaml").write_text("name: synthetic_client\npublish_to: none\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    before = (client / ".git/config").read_bytes()
    assert (
        build_tools(root).execute(SimpleTool(action="projects"))
        == f"pokerlost/pokerClient: {client}"
    )
    assert not (client / ".git/index").exists()
    assert (client / ".git/config").read_bytes() == before
    assert not list(client.glob("*.py")) and not (client / ".venv").exists()
    assert not (home / "Projects/serrja/pythonTemplate").exists()
