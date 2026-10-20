"""Real Copier and Git checks for the trusted, versioned templates boundary."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from setup_system.application import templates
from setup_system.application.project_catalog import TemplateMetadata, TemplateVersion
from setup_system.cli import main


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, text=True, capture_output=True
    ).stdout.strip()


def commit(path: Path, message: str) -> str:
    git(path, "add", ".")
    git(
        path,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "--quiet",
        "-m",
        message,
    )
    return git(path, "rev-parse", "HEAD")


def tag(path: Path, name: str) -> None:
    git(
        path,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "tag",
        "-a",
        name,
        "-m",
        name,
    )


@pytest.fixture
def source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> templates.TemplateSource:
    repository = tmp_path / "source"
    repository.mkdir()
    git(repository, "init", "--quiet")
    (repository / "copier.yml").write_text(
        '_min_copier_version: "9.17.2"\n_subdirectory: template\n'
        '_skip_if_exists: [settings.py, "migrations/**"]\n'
        "_tasks: []\n"
        "projectName:\n  type: str\n  default: example\n"
    )
    template = repository / "template"
    template.mkdir()
    (template / ".copier-answers.yml.jinja").write_text("{{ _copier_answers | to_nice_yaml -}}\n")
    (template / "identity.json.jinja").write_text(
        '{"sourceCommit": "{{ _copier_conf.vcs_ref_hash }}", '
        '"version": "{{ _copier_answers._commit }}"}\n'
    )
    (template / "README.md.jinja").write_text("# {{ projectName }}\n\nInitial documentation.\n")
    (template / "app.py").write_text("# Template API\n\ndef answer():\n    return 1\n")
    (template / "settings.py").write_text("CUSTOM = False\n")
    (template / "tests").mkdir()
    (template / "tests/test_seed.py").write_text("def test_seed():\n    assert True\n")
    (template / "migrations").mkdir()
    (template / "migrations/001.sql").write_text("-- seed migration\n")
    (repository / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="0.1.0"\n')
    first = commit(repository, "version one")
    tag(repository, "v0.1.0")
    (template / "README.md.jinja").write_text("# {{ projectName }}\n\nUpdated documentation.\n")
    (repository / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="0.2.0"\n')
    second = commit(repository, "version two")
    tag(repository, "v0.2.0")
    original_fetch = templates._fetch_ref
    monkeypatch.setattr(
        templates,
        "_fetch_ref",
        lambda target, url, ref: original_fetch(target, str(repository), ref),
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return templates.TemplateSource(
        project="fixture/template",
        url="https://github.com/fixture/template.git",
        metadata=TemplateMetadata(
            alias="python",
            recommendedVersion="0.2.0",
            versions=(
                TemplateVersion(version="0.1.0", tag="v0.1.0", commit=first),
                TemplateVersion(version="0.2.0", tag="v0.2.0", commit=second),
            ),
        ),
    )


def plan(
    source: templates.TemplateSource, destination: Path, **options: object
) -> templates.TemplatePlan:
    values = {"template": source.metadata.alias, "version": "0.1.0", "defaults": True, **options}
    return templates.plan_template(
        {source.metadata.alias: source}, destination=destination, **values
    )


def test_real_copy_and_offline_update_preserve_project_files_and_refs(
    tmp_path: Path, source: templates.TemplateSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "generated"
    environment = dict(os.environ)
    result = templates.execute_template(plan(source, destination))
    assert result.status == "created" and not (destination / ".git").exists()
    assert not (destination / ".venv").exists() and not (destination / "TASK_MUST_NOT_RUN").exists()
    answers = yaml.safe_load((destination / ".copier-answers.yml").read_text())
    assert answers["_src_path"] == source.url and answers["_commit"] == "v0.1.0"
    assert (
        json.loads((destination / "identity.json").read_text())["sourceCommit"]
        == result.source_commit
    )
    assert dict(os.environ) == environment
    git(destination, "init", "--quiet")
    commit(destination, "generated project")
    owned = {
        "app.py": (destination / "app.py").read_text() + "\nUSER_CODE = 42\n",
        "settings.py": "CUSTOM = True\n",
        "tests/test_user.py": "def test_user():\n    assert 42 == 42\n",
        "migrations/001.sql": "-- user-owned applied revision\n",
        "migrations/002.sql": "-- another applied revision\n",
    }
    for name, content in owned.items():
        (destination / name).write_text(content)
    head = commit(destination, "user changes")
    refs = git(destination, "show-ref")
    monkeypatch.setattr(templates, "_fetch_ref", lambda *args: pytest.fail("offline fetch"))
    result = templates.execute_template(
        plan(source, destination, operation="update", version="0.2.0", offline=True)
    )
    assert result.status == "updated"
    assert git(destination, "rev-parse", "HEAD") == head and git(destination, "show-ref") == refs
    assert "Updated documentation" in (destination / "README.md").read_text()
    for name, content in owned.items():
        assert (destination / name).read_text() == content
    answers = yaml.safe_load((destination / ".copier-answers.yml").read_text())
    assert answers["_commit"] == "v0.2.0" and answers["_src_path"] == source.url
    assert not (destination / "TASK_MUST_NOT_RUN").exists()
    assert not git(destination, "remote")


def test_cli_update_accepts_parent_relative_destination(
    tmp_path: Path, source: templates.TemplateSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setattr(main, "load_templates", lambda root: {"python": source})
    runner = CliRunner()
    options = ["--template", "python", "--destination", "../project", "--defaults"]
    created = runner.invoke(main.app, ["templates", "create", *options, "--version", "0.1.0"])
    assert created.exit_code == 0, created.output
    destination = tmp_path / "project"
    git(destination, "init", "--quiet")
    head = commit(destination, "generated project")
    updated = runner.invoke(
        main.app,
        ["templates", "update", *options, "--version", "0.2.0", "--offline"],
    )
    assert updated.exit_code == 0, updated.output
    assert "Updated documentation" in (destination / "README.md").read_text()
    assert git(destination, "rev-parse", "HEAD") == head


def test_destination_and_missing_offline_cache_fail_before_writes(
    tmp_path: Path, source: templates.TemplateSource
) -> None:
    destination = tmp_path / "owned"
    destination.mkdir()
    (destination / "keep.txt").write_text("user data")
    with pytest.raises(ValueError, match="empty"):
        templates.execute_template(plan(source, destination))
    assert list(destination.iterdir()) == [destination / "keep.txt"]
    absent = tmp_path / "absent"
    with pytest.raises(ValueError, match=r"Offline.*missing"):
        templates.execute_template(plan(source, absent, offline=True))
    assert not absent.exists() and not (tmp_path / "cache").exists()


@pytest.mark.parametrize("version", ["", "main", "latest", "v0.1.0", "0.99.0"])
def test_only_catalog_versions_are_accepted(
    source: templates.TemplateSource, tmp_path: Path, version: str
) -> None:
    with pytest.raises(ValueError, match="reviewed version"):
        plan(source, tmp_path / "absent", version=version)


def test_bad_tag_and_corrupt_cache_fail_closed(
    tmp_path: Path, source: templates.TemplateSource
) -> None:
    bad = source.model_copy(
        update={
            "metadata": source.metadata.model_copy(
                update={
                    "versions": (
                        source.metadata.versions[0].model_copy(update={"commit": "a" * 40}),
                    ),
                    "recommended_version": "0.1.0",
                }
            )
        }
    )
    with pytest.raises(ValueError, match="mismatch"):
        templates.prepare_cache(plan(bad, tmp_path / "absent"))
    correct = plan(source, tmp_path / "absent")
    cached = templates.prepare_cache(correct)
    (cached.parent / "catalog.json").write_text("corrupt")
    with pytest.raises(ValueError, match="corrupt"):
        templates.execute_template(correct)
    assert not correct.destination.exists()


def test_cli_lists_versions_defaults_and_real_create(
    tmp_path: Path, source: templates.TemplateSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "load_templates", lambda root: {"python": source})
    runner = CliRunner()
    versions = runner.invoke(main.app, ["templates", "versions", "python"])
    assert (
        versions.exit_code == 0
        and "0.2.0 v0.2.0" in versions.output
        and "recommended" in versions.output
    )
    destination = tmp_path / "cli"
    result = runner.invoke(
        main.app,
        [
            "templates",
            "create",
            "--template",
            "python",
            "--defaults",
            "--destination",
            str(destination),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "template python 0.2.0" in result.output
    assert "templates update" in result.output
    assert "uv lock\nuv sync --locked" in result.output
    assert "python -c 'import example'" in result.output
    assert yaml.safe_load((destination / ".copier-answers.yml").read_text())["_commit"] == "v0.2.0"
    rejected = runner.invoke(main.app, ["templates", "create", "--defaults"])
    assert rejected.exit_code == 2 and "--template" in rejected.output


def test_update_rejects_dirty_project_and_untrusted_answers(
    tmp_path: Path, source: templates.TemplateSource
) -> None:
    destination = tmp_path / "project"
    templates.execute_template(plan(source, destination))
    git(destination, "init", "--quiet")
    with pytest.raises(ValueError, match="preserve project changes"):
        templates.execute_template(plan(source, destination, operation="update"))
    answers = destination / ".copier-answers.yml"
    answers.write_text(
        answers.read_text().replace(source.url, "https://untrusted.invalid/template")
    )
    commit(destination, "untrusted answers")
    with pytest.raises(ValueError, match="reviewed template source"):
        templates.execute_template(plan(source, destination, operation="update"))


def test_lightweight_tag_is_rejected(tmp_path: Path, source: templates.TemplateSource) -> None:
    repository = tmp_path / "source"
    git(repository, "tag", "-d", "v0.1.0")
    git(repository, "tag", "v0.1.0", source.metadata.versions[0].commit)
    with pytest.raises(ValueError, match="annotated tag"):
        templates.execute_template(plan(source, tmp_path / "absent"))
    assert not (tmp_path / "absent").exists()


def test_create_does_not_execute_template_tasks(
    tmp_path: Path, source: templates.TemplateSource
) -> None:
    repository = tmp_path / "source"
    config = repository / "copier.yml"
    config.write_text(
        config.read_text().replace("_tasks: []", '_tasks: ["touch TASK_MUST_NOT_RUN"]')
    )
    (repository / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="0.3.0"\n')
    third = commit(repository, "task fixture")
    tag(repository, "v0.3.0")
    metadata = TemplateMetadata(
        alias="python",
        recommendedVersion="0.3.0",
        versions=(TemplateVersion(version="0.3.0", tag="v0.3.0", commit=third),),
    )
    with_task = source.model_copy(update={"metadata": metadata})
    destination = tmp_path / "task-project"
    templates.execute_template(plan(with_task, destination, version=None))
    assert not (destination / "TASK_MUST_NOT_RUN").exists()
    assert not (destination / ".git").exists()


def test_copier_merge_conflict_is_not_reported_as_success(
    tmp_path: Path, source: templates.TemplateSource
) -> None:
    destination = tmp_path / "conflict"
    templates.execute_template(plan(source, destination))
    git(destination, "init", "--quiet")
    commit(destination, "generated")
    (destination / "README.md").write_text("# example\n\nUser documentation.\n")
    before = commit(destination, "user documentation")
    result = templates.execute_template(
        plan(source, destination, operation="update", version="0.2.0")
    )
    assert result.status == "conflicts"
    assert git(destination, "rev-parse", "HEAD") == before
    assert "User documentation." in (destination / "README.md").read_text()


def test_cli_interactive_selection_and_explicit_answers(
    tmp_path: Path, source: templates.TemplateSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "load_templates", lambda root: {"python": source})
    destination = tmp_path / "interactive"
    prompts = iter(["python", "0.2.0", str(destination)])
    monkeypatch.setattr(main.typer, "prompt", lambda message, **kwargs: next(prompts))
    import sys

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    answers = tmp_path / "answers.json"
    answers.write_text('{"projectName": "interactiveProject"}')
    # Call the boundary directly so CliRunner does not replace the terminal probe.
    main.templates_create(
        template=None, version=None, destination=None, data=answers, defaults=True, offline=False
    )
    assert "interactiveProject" in (destination / "README.md").read_text()


def test_internal_answers_and_symlink_destinations_are_rejected(
    tmp_path: Path, source: templates.TemplateSource
) -> None:
    with pytest.raises(ValueError, match="Internal Copier"):
        plan(source, tmp_path / "absent", data={"_src_path": "https://untrusted.invalid"})
    target = tmp_path / "real"
    target.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        templates.execute_template(plan(source, linked / "project"))
    assert list(target.iterdir()) == []


def test_catalog_rejects_source_substitution(tmp_path: Path) -> None:
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    (manifests / "projects.toml").write_text(
        'schemaVersion=1\nprojectsRoot="$HOME/Projects"\nownerGroups=["fixture"]\n'
        '[[project]]\nid="fixture/template"\nstate="existing"\n'
        'origin="https://untrusted.invalid/template.git"\n'
        '[project.template]\nalias="python"\n'
    )
    with pytest.raises(ValueError, match="canonical GitHub"):
        templates.load_templates(tmp_path)


def test_cache_rejects_symlinked_repository(
    tmp_path: Path, source: templates.TemplateSource
) -> None:
    selected = plan(source, tmp_path / "absent")
    repository = templates.prepare_cache(selected)
    outside = tmp_path / "outside.git"
    repository.rename(outside)
    repository.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        templates.execute_template(selected)
    assert not selected.destination.exists()


def test_update_rejects_symlinked_answers(tmp_path: Path, source: templates.TemplateSource) -> None:
    destination = tmp_path / "project"
    templates.execute_template(plan(source, destination))
    answers = destination / ".copier-answers.yml"
    external = tmp_path / "external-answers.yml"
    answers.rename(external)
    original = external.read_bytes()
    answers.symlink_to(external)
    git(destination, "init", "--quiet")
    commit(destination, "linked answers")
    with pytest.raises(ValueError, match="Valid Copier answers"):
        templates.execute_template(plan(source, destination, operation="update", version="0.2.0"))
    assert external.read_bytes() == original and not git(destination, "status", "--porcelain")


def test_service_uses_recorded_identity_without_hidden_copier_default(
    tmp_path: Path, source: templates.TemplateSource
) -> None:
    repository = tmp_path / "source"
    config = repository / "copier.yml"
    config.write_text(
        config.read_text().replace(
            "projectName:\n  type: str\n  default: example\n",
            "service_name:\n  type: str\n  default: exampleService\n"
            'projectName:\n  type: str\n  default: "{{ service_name }}"\n  when: false\n',
        )
    )
    (repository / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="0.3.0"\n')
    third = commit(repository, "service identity fixture")
    tag(repository, "v0.3.0")
    service = source.model_copy(
        update={
            "metadata": TemplateMetadata(
                alias="service",
                recommendedVersion="0.3.0",
                versions=(TemplateVersion(version="0.3.0", tag="v0.3.0", commit=third),),
            )
        }
    )
    destination = tmp_path / "service-project"
    result = templates.execute_template(plan(service, destination, version=None))
    answers = yaml.safe_load((destination / ".copier-answers.yml").read_text())
    assert "projectName" not in answers and answers["service_name"] == "exampleService"
    assert result.project_name == "exampleService" and result.status == "created"
    assert not (destination / ".git").exists() and not (destination / ".venv").exists()
