"""Real Copier and Git checks for the trusted, versioned templates boundary."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Literal

import pytest
import yaml
from pydantic import JsonValue

from setup_system.application import templates
from setup_system.domain.projects import (
    TemplateMetadata,
    TemplateVersion,
    python_version_for_git_tag,
)
from setup_system.domain.templates import TemplatePlan, TemplateSource
from setup_system.infrastructure import copier_templates


@pytest.mark.parametrize(
    ("native", "tag"),
    [("1.2.3", "v1.2.3"), ("1.2.3.dev0", "v1.2.3-dev.0"), ("1.2.3.dev12", "v1.2.3-dev.12")],
)
def test_native_versions_round_trip_with_github_tags(native: str, tag: str) -> None:
    assert python_version_for_git_tag(tag) == native
    assert TemplateVersion(version=native, tag=tag, commit="a" * 40).version == native


@pytest.mark.parametrize("native", ["1.2.3.dev0", "1.2.3rc1", "1.2.3.post1", "1.2.3.post0.dev1"])
def test_existing_pep440_tags_remain_accepted(native: str) -> None:
    assert python_version_for_git_tag("v" + native) == native
    assert TemplateVersion(version=native, tag="v" + native, commit="a" * 40).tag == "v" + native


@pytest.mark.parametrize(
    "tag", ["main", "v1.2.3-dev.-1", "v1.2.3-dev.01", "v1.2.3+local", "v1.2.3-dev.2/other"]
)
def test_invalid_git_versions_are_rejected(tag: str) -> None:
    with pytest.raises(ValueError):
        python_version_for_git_tag(tag)


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
def source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TemplateSource:
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
    original_fetch = copier_templates._fetch_ref
    monkeypatch.setattr(
        copier_templates,
        "_fetch_ref",
        lambda target, url, ref: original_fetch(target, str(repository), ref),
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return TemplateSource(
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
    source: TemplateSource,
    destination: Path,
    *,
    operation: Literal["create", "update"] = "create",
    version: str | None = "0.1.0",
    offline: bool = False,
    data: dict[str, JsonValue] | None = None,
) -> TemplatePlan:
    return templates.plan_template(
        {source.metadata.alias: source},
        template=source.metadata.alias,
        destination=destination,
        version=version,
        operation=operation,
        offline=offline,
        defaults=True,
        data=data,
    )


def test_real_copy_and_offline_update_preserve_project_files_and_refs(
    tmp_path: Path, source: TemplateSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "generated"
    environment = dict(os.environ)
    result = copier_templates.execute_template(plan(source, destination))
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
    monkeypatch.setattr(copier_templates, "_fetch_ref", lambda *args: pytest.fail("offline fetch"))
    result = copier_templates.execute_template(
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


def test_destination_and_missing_offline_cache_fail_before_writes(
    tmp_path: Path, source: TemplateSource
) -> None:
    destination = tmp_path / "owned"
    destination.mkdir()
    (destination / "keep.txt").write_text("user data")
    with pytest.raises(ValueError, match="empty"):
        copier_templates.execute_template(plan(source, destination))
    assert list(destination.iterdir()) == [destination / "keep.txt"]
    absent = tmp_path / "absent"
    with pytest.raises(ValueError, match=r"Offline.*missing"):
        copier_templates.execute_template(plan(source, absent, offline=True))
    assert not absent.exists() and not (tmp_path / "cache").exists()


@pytest.mark.parametrize("version", ["", "main", "latest", "v0.1.0", "0.99.0"])
def test_only_catalog_versions_are_accepted(
    source: TemplateSource, tmp_path: Path, version: str
) -> None:
    with pytest.raises(ValueError, match="reviewed version"):
        plan(source, tmp_path / "absent", version=version)


def test_bad_tag_and_corrupt_cache_fail_closed(tmp_path: Path, source: TemplateSource) -> None:
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
        copier_templates.prepare_cache(plan(bad, tmp_path / "absent"))
    correct = plan(source, tmp_path / "absent")
    cached = copier_templates.prepare_cache(correct)
    (cached.parent / "catalog.json").write_text("corrupt")
    with pytest.raises(ValueError, match="corrupt"):
        copier_templates.execute_template(correct)
    assert not correct.destination.exists()


def test_update_rejects_dirty_project_and_untrusted_answers(
    tmp_path: Path, source: TemplateSource
) -> None:
    destination = tmp_path / "project"
    copier_templates.execute_template(plan(source, destination))
    git(destination, "init", "--quiet")
    with pytest.raises(ValueError, match="preserve project changes"):
        copier_templates.execute_template(plan(source, destination, operation="update"))
    answers = destination / ".copier-answers.yml"
    answers.write_text(
        answers.read_text().replace(source.url, "https://untrusted.invalid/template")
    )
    commit(destination, "untrusted answers")
    with pytest.raises(ValueError, match="reviewed template source"):
        copier_templates.execute_template(plan(source, destination, operation="update"))


def test_lightweight_tag_is_rejected(tmp_path: Path, source: TemplateSource) -> None:
    repository = tmp_path / "source"
    git(repository, "tag", "-d", "v0.1.0")
    git(repository, "tag", "v0.1.0", source.metadata.versions[0].commit)
    with pytest.raises(ValueError, match="annotated tag"):
        copier_templates.execute_template(plan(source, tmp_path / "absent"))
    assert not (tmp_path / "absent").exists()


def test_create_does_not_execute_template_tasks(tmp_path: Path, source: TemplateSource) -> None:
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
    copier_templates.execute_template(plan(with_task, destination, version=None))
    assert not (destination / "TASK_MUST_NOT_RUN").exists()
    assert not (destination / ".git").exists()


def test_copier_merge_conflict_is_not_reported_as_success(
    tmp_path: Path, source: TemplateSource
) -> None:
    destination = tmp_path / "conflict"
    copier_templates.execute_template(plan(source, destination))
    git(destination, "init", "--quiet")
    commit(destination, "generated")
    (destination / "README.md").write_text("# example\n\nUser documentation.\n")
    before = commit(destination, "user documentation")
    result = copier_templates.execute_template(
        plan(source, destination, operation="update", version="0.2.0")
    )
    assert result.status == "conflicts"
    assert git(destination, "rev-parse", "HEAD") == before
    assert "User documentation." in (destination / "README.md").read_text()


def test_internal_answers_and_symlink_destinations_are_rejected(
    tmp_path: Path, source: TemplateSource
) -> None:
    with pytest.raises(ValueError, match="Internal Copier"):
        plan(source, tmp_path / "absent", data={"_src_path": "https://untrusted.invalid"})
    target = tmp_path / "real"
    target.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        copier_templates.execute_template(plan(source, linked / "project"))
    assert list(target.iterdir()) == []


def test_catalog_rejects_source_substitution(tmp_path: Path) -> None:
    manifests = tmp_path / "config/workspace"
    manifests.mkdir(parents=True)
    (manifests / "projects.toml").write_text(
        'schemaVersion=1\nprojectsRoot="$HOME/Projects"\nownerGroups=["fixture"]\n'
        '[[project]]\nid="fixture/template"\n'
        'origin="https://untrusted.invalid/template.git"\n'
        '[project.template]\nalias="python"\n'
    )
    with pytest.raises(ValueError, match="canonical GitHub"):
        templates.load_templates(tmp_path)


def test_cache_rejects_symlinked_repository(tmp_path: Path, source: TemplateSource) -> None:
    selected = plan(source, tmp_path / "absent")
    repository = copier_templates.prepare_cache(selected)
    outside = tmp_path / "outside.git"
    repository.rename(outside)
    repository.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        copier_templates.execute_template(selected)
    assert not selected.destination.exists()


def test_update_rejects_symlinked_answers(tmp_path: Path, source: TemplateSource) -> None:
    destination = tmp_path / "project"
    copier_templates.execute_template(plan(source, destination))
    answers = destination / ".copier-answers.yml"
    external = tmp_path / "external-answers.yml"
    answers.rename(external)
    original = external.read_bytes()
    answers.symlink_to(external)
    git(destination, "init", "--quiet")
    commit(destination, "linked answers")
    with pytest.raises(ValueError, match="Valid Copier answers"):
        copier_templates.execute_template(
            plan(source, destination, operation="update", version="0.2.0")
        )
    assert external.read_bytes() == original and not git(destination, "status", "--porcelain")


def test_service_uses_recorded_identity_without_hidden_copier_default(
    tmp_path: Path, source: TemplateSource
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
    result = copier_templates.execute_template(plan(service, destination, version=None))
    answers = yaml.safe_load((destination / ".copier-answers.yml").read_text())
    assert "projectName" not in answers and answers["service_name"] == "exampleService"
    assert result.project_name == "exampleService" and result.status == "created"
    assert not (destination / ".git").exists() and not (destination / ".venv").exists()


def test_current_github_dev_tag_verifies_native_metadata_and_copier_answers(
    tmp_path: Path, source: TemplateSource
) -> None:
    repository = tmp_path / "source"
    (repository / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="0.3.0.dev2"\n')
    head = commit(repository, "native dev metadata")
    tag(repository, "v0.3.0-dev.2")
    current = source.model_copy(
        update={
            "metadata": TemplateMetadata(
                alias="python",
                recommendedVersion="0.3.0.dev2",
                versions=(TemplateVersion(version="0.3.0.dev2", tag="v0.3.0-dev.2", commit=head),),
            )
        }
    )
    destination = tmp_path / "dev tag with spaces"
    result = copier_templates.execute_template(plan(current, destination, version=None))
    assert result.source_commit == head and result.version == "0.3.0.dev2"
    answers = yaml.safe_load((destination / ".copier-answers.yml").read_text())
    assert answers["_commit"] == "v0.3.0-dev.2"


def test_make_template_uses_absolute_destination_and_preserves_answers(
    tmp_path: Path, source: TemplateSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    from setup_system.project_tasks import execute

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(templates, "load_templates", lambda root: {"python": source})
    destination = tmp_path / "user project"
    request = {
        "schema": 1,
        "expected": None,
        "settings": {
            "template": "python",
            "destination": str(destination),
            "project_name": "interactiveProject",
        },
    }
    response = execute(
        tmp_path,
        "template-versions",
        {"schema": 1, "expected": None, "settings": {"template": "python"}},
    )
    assert "0.2.0" in response.observed["versions"]
    with pytest.raises(ValueError):
        execute(tmp_path, "template-create-apply", request)
    assert not destination.exists()
    planned = execute(tmp_path, "template-create-plan", request)
    assert planned.observed["version"] == "0.2.0" and not destination.exists()
    assert (
        execute(
            tmp_path,
            "template-create-apply",
            {**request, "expected": planned.model_dump(mode="json", exclude_unset=True)},
        ).status
        == "complete"
    )
    assert "interactiveProject" in (destination / "README.md").read_text()
    assert yaml.safe_load((destination / ".copier-answers.yml").read_text())["_commit"] == "v0.2.0"
    git(destination, "init", "--quiet")
    commit(destination, "generated")
    preserved = destination / "my-file"
    preserved.write_text("private project data")
    commit(destination, "personal")
    before = git(destination, "rev-parse", "HEAD")
    update = {
        "schema": 1,
        "expected": None,
        "settings": {"template": "python", "version": "0.2.0", "destination": str(destination)},
    }
    preview = execute(tmp_path, "template-update-plan", update)
    assert (
        execute(
            tmp_path,
            "template-update-apply",
            {**update, "expected": preview.model_dump(mode="json", exclude_unset=True)},
        ).status
        == "complete"
    )
    assert (
        git(destination, "rev-parse", "HEAD") == before
        and preserved.read_text() == "private project data"
    )


def test_cached_release_lookup_has_no_checkout_or_network_dependency(
    tmp_path: Path, source: TemplateSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    from setup_system.domain.projects import TemplateMetadata

    bare = TemplateSource(
        project=source.project, url=source.url, metadata=TemplateMetadata(alias="python")
    )
    prepared = plan(source, tmp_path / "generated", version="0.2.0")
    copier_templates.prepare_cache(prepared)
    monkeypatch.setattr(copier_templates, "_fetch_ref", lambda *args: pytest.fail("network access"))
    cached = copier_templates.available_versions(bare, prepared.cache_root, offline=True)
    assert [r.version for r in cached] == ["0.2.0", "0.1.0"]
    choice = copier_templates.selected_source(
        bare, prepared.cache_root, None, tmp_path, update=False, offline=True
    )
    assert choice.metadata.recommended_version == "0.2.0"
    offline = templates.plan_template(
        {"python": choice},
        template="python",
        version=None,
        destination=tmp_path / "offline copy",
        offline=True,
        defaults=True,
    )
    assert copier_templates.execute_template(offline).status == "created"


def test_public_git_and_local_copier_do_not_inherit_ambient_credentials(
    tmp_path, monkeypatch, source
):
    import os
    from types import SimpleNamespace

    for key in ("SSH_AUTH_SOCK", "SSH_ASKPASS", "GH_TOKEN", "HTTPS_PROXY", "GIT_CONFIG_PARAMETERS"):
        monkeypatch.setenv(key, "synthetic-private-value")
    monkeypatch.setenv("HOME", str(tmp_path))
    bare = TemplateSource(
        project=source.project,
        url=source.url,
        metadata=TemplateMetadata(alias=source.metadata.alias),
    )
    destination = tmp_path / "destination"
    selected = plan(source, destination)
    checkout = tmp_path / "verified.git"
    monkeypatch.setattr(copier_templates, "copier_executable", lambda: tmp_path / "official-copier")
    calls = []

    def capture(argv, *, env, **kwargs):
        assert not any(value == "synthetic-private-value" for value in env.values())
        assert env["HOME"] != os.environ["HOME"]
        assert env["GIT_ASKPASS"] == "/bin/false" and env["GIT_CONFIG_GLOBAL"] == "/dev/null"
        calls.append((argv, env))
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(copier_templates.subprocess, "run", capture)
    assert copier_templates.available_versions(bare, tmp_path / "cache", offline=False) == ()
    copier_templates._run_copier(selected, checkout, destination)
    git, copier = calls
    assert "http.followRedirects=false" in git[0] and git[1]["HOME"] == "/dev/null"
    assert git[0][-2] == source.url and git[1]["GIT_TERMINAL_PROMPT"] == "0"
    assert copier[1]["GIT_ALLOW_PROTOCOL"] == "file" and "--skip-tasks" in copier[0]
