"""Exercise the actual PR guard and immutable native release integration."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("base", "head", "head_repo", "title", "accepted"),
    [
        ("test", "refactorG03HyprlandSetup", "serrja/setupSystem", "feat(setup): install", True),
        ("dev", "test", "serrja/setupSystem", "chore(release): promote test", True),
        ("main", "dev", "serrja/setupSystem", "chore(release): promote dev", True),
        ("test", "feat/oldBranch", "serrja/setupSystem", "feat(setup): install", False),
        ("test", "main", "serrja/setupSystem", "feat(setup): install", False),
        ("test", "dev", "serrja/setupSystem", "feat(setup): install", False),
        ("dev", "refactorG03HyprlandSetup", "serrja/setupSystem", "feat(setup): install", False),
        ("main", "test", "serrja/setupSystem", "chore(release): promote test", False),
        ("dev", "test", "other/setupSystem", "chore(release): promote test", False),
        ("main", "dev", "other/setupSystem", "chore(release): promote dev", False),
        ("test", "refactorG03", "serrja/setupSystem", "feat: install\nextra", False),
        ("test", "refactorG03", "serrja/setupSystem", "Unstructured title", False),
    ],
)
def test_actual_contribution_guard(
    base: str, head: str, head_repo: str, title: str, accepted: bool
) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/contribution.yml").read_text())
    steps = workflow["jobs"]["contribution"]["steps"]
    assert all("uses" not in step for step in steps)
    result = subprocess.run(
        ["bash", "-c", steps[0]["run"]],
        env={
            **os.environ,
            "BASE_REPO": "serrja/setupSystem",
            "HEAD_REPO": head_repo,
            "BASE_REF": base,
            "HEAD_REF": head,
            "PR_TITLE": title,
        },
        check=False,
        timeout=10,
    )
    assert (result.returncode == 0) is accepted


def test_release_uses_pinned_provider_and_unprivileged_builder() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers["push"]["branches"]) == {"test", "dev", "main"}
    assert set(triggers["pull_request"]["branches"]) == {"test", "dev", "main"}
    assert "paths" not in triggers["push"]
    jobs = workflow["jobs"]
    for job, action in (
        ("release-allocation", "python-allocate.yml"),
        ("release-publication", "python-publish.yml"),
    ):
        pin = "e5cac33af228224af9deaef308e39c1b9f788033"
        assert jobs[job]["uses"] == f"pokerlost/.github/.github/workflows/{action}@{pin}"
        assert jobs[job]["with"]["provider-ref"] == pin
        assert jobs[job]["with"]["source-workflow"] == ".github/workflows/ci.yml"
        assert jobs[job]["with"]["preflight-gate"] == "required"
        assert "secrets" not in jobs[job]
    assert jobs["release-builder"]["permissions"]["contents"] == "read"
    assert jobs["release-builder"]["permissions"]["id-token"] == "write"
    assert set(jobs["required"]["needs"]) == {"verify", "compatibility"}


def test_native_helper_matches_reviewed_snapshot() -> None:
    source = json.loads((ROOT / ".github/engineering-source.json").read_text())
    helper = source["files"]["docker/python_release.py"]
    assert helper["revision"] == "8bb73ec1eada479003785d362aeb4b9d382e1194"
    assert (
        hashlib.sha256((ROOT / "docker/python_release.py").read_bytes()).hexdigest()
        == (helper["sha256"])
    )


def test_isolated_build_constraints_cover_the_complete_locked_backend_graph() -> None:
    source = tomllib.loads((ROOT / "pyproject.toml").read_text())
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    packages = {canonicalize_name(item["name"]): item for item in lock["package"]}
    pending = [Requirement(value) for value in source["build-system"]["requires"]]
    expected: dict[str, str] = {}
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        package = packages[name]
        version = package["version"]
        assert Version(version) in requirement.specifier
        if name in expected:
            continue
        expected[name] = version
        pending.extend(Requirement(item["name"]) for item in package.get("dependencies", []))
    actual: dict[str, str] = {}
    for value in source["tool"]["uv"]["build-constraint-dependencies"]:
        requirement = Requirement(value)
        assert requirement.url is None and requirement.marker is None
        specifiers = list(requirement.specifier)
        assert len(specifiers) == 1 and specifiers[0].operator == "=="
        name = canonicalize_name(requirement.name)
        assert name not in actual
        actual[name] = specifiers[0].version
    assert actual == expected
    assert {item["name"]: item["specifier"] for item in lock["manifest"]["build-constraints"]} == {
        name: "==" + version for name, version in expected.items()
    }


@pytest.mark.parametrize("docker_child", [False, True])
def test_make_check_routes_native_and_docker_children_without_executing_recipes(
    tmp_path: Path, docker_child: bool
) -> None:
    import shutil

    (tmp_path / "docker").mkdir()
    for name in ("Makefile", "docker/quality.mk", "docker/quality-python.mk"):
        shutil.copyfile(ROOT / name, tmp_path / name)
    command = [
        "make",
        "--no-print-directory",
        "--dry-run",
        "check",
        "CONTAINERIZED=0",
        "SHELL=/bin/false",
        "QUALITY_IMAGE=setupsystem-g03:routing-fixture",
    ]
    if docker_child:
        command.append("SKIP_QUALITY_BUILD=1")
    environment = dict(os.environ)
    for key in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL"):
        environment.pop(key, None)
    result = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    # A recursive recipe would hit the false shell and fail. No installer, Docker
    # command, report writer or project shell is executed by this expansion test.
    assert result.returncode == 0, result.stderr
    assert ("docker run --init" in result.stdout) is docker_child
    assert ("uv run --locked pytest" in result.stdout) is not docker_child
    assert not (tmp_path / "coverage").exists()
