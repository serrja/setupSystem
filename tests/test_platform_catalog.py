"""New platform inventory is data, never permission to move or initialize Git."""

from pathlib import Path

from setup_system.application.project_layout import load_layout

ROOT = Path(__file__).resolve().parents[1]


def test_platform_catalog_load_has_no_filesystem_side_effect(tmp_path: Path) -> None:
    home = tmp_path / "synthetic-home"
    home.mkdir()
    before = list(home.iterdir())
    layout = load_layout(ROOT, home=home)
    expected = {"pokerWeb", "pokerGateway", "platformInfra"}
    actual = {
        project.id.split("/")[1]: project
        for project in layout.projects
        if project.id in {"pokerlost/" + name for name in expected}
    }
    assert set(actual) == expected
    for name, project in actual.items():
        assert project.target == home / "Projects/pokerlost" / name
        assert project.origin == "git@github.com:pokerlost/" + name + ".git"
        assert project.state == "existing" and project.legacy_path is None
    assert list(home.iterdir()) == before
    assert layout.owner_groups == ("serrja", "pokerlost")
