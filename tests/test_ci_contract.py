"""Exercise the actual PR guard and immutable native release integration."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
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
        pin = jobs["release-allocation"]["with"]["provider-ref"]
        assert re.fullmatch(r"[0-9a-f]{40}", pin)
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
    assert re.fullmatch(r"[0-9a-f]{40}", helper["revision"])
    assert (
        hashlib.sha256((ROOT / "docker/python_release.py").read_bytes()).hexdigest()
        == (helper["sha256"])
    )
    assert source["repository"] == "https://github.com/serrja/pythonTemplate"
    for name, record in source["files"].items():
        assert re.fullmatch(r"[0-9a-f]{40}", record.get("revision", source["revision"]))
        data = (ROOT / name).read_bytes()
        if name == "tests/test_python_release.py":
            # Reverse the recorded fixture-path adaptation to verify the upstream digest.
            old = b'ROOT / "docker/python_release.py"'
            assert data.count(old) == 1
            data = data.replace(old, b'ROOT / "template/docker/python_release.py"')
            data = subprocess.check_output(
                [
                    str(Path(sys.executable).with_name("ruff")),
                    "format",
                    "--isolated",
                    "--no-cache",
                    "--line-length",
                    "88",
                    "--stdin-filename",
                    name,
                    "-",
                ],
                input=data,
            )
            expected = record["sourceSha256"]
        else:
            expected = record["sha256"]
        assert hashlib.sha256(data).hexdigest() == expected, name


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
    shutil.copytree(ROOT / "mk", tmp_path / "mk")
    for name in ("Makefile", "docker/quality.mk", "docker/quality-python.mk"):
        shutil.copyfile(ROOT / name, tmp_path / name)
    command = [
        "make",
        "--no-print-directory",
        "--dry-run",
        "SHELL=/bin/false",
        "QUALITY_IMAGE=setupsystem-g03:routing-fixture",
    ]
    if docker_child:
        command.extend(["-f", "docker/quality-python.mk", "test", "SKIP_QUALITY_BUILD=1"])
    else:
        command.append("native-check")
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
    for name in ("run", "base-inspect", "workspace-plan"):
        public = subprocess.check_output(
            ["make", "--no-print-directory", "-n", name], cwd=tmp_path, env=environment, text=True
        )
        assert "docker" not in public and "--locked pytest" not in public
    assert not (tmp_path / "coverage").exists()


def test_make_sanitizes_git_routing_for_pytest_only(tmp_path: Path) -> None:
    import shutil

    (tmp_path / "docker").mkdir()
    shutil.copytree(ROOT / "mk", tmp_path / "mk")
    for name in ("Makefile", "docker/quality.mk", "docker/quality-python.mk"):
        shutil.copyfile(ROOT / name, tmp_path / name)
    fake_uv = tmp_path / "fixture-uv"
    fake_uv.write_text(
        "#!/bin/sh\nset -eu\n"
        '[ "${GIT_DIR+x}" != x ]\n'
        '[ "${GIT_WORK_TREE+x}" != x ]\n'
        '[ "${GIT_COMMON_DIR+x}" != x ]\n'
        'printf "closed Git context for pytest\\n"\n'
    )
    fake_uv.chmod(0o700)
    environment = {
        **os.environ,
        "GIT_DIR": "/fixture/outer.git",
        "GIT_WORK_TREE": "/fixture/outer",
        "GIT_COMMON_DIR": "/fixture/common.git",
    }
    for key in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL"):
        environment.pop(key, None)
    result = subprocess.run(
        ["make", "--no-print-directory", "test", "CONTAINERIZED=1", f"UV={fake_uv}"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "closed Git context for pytest" in result.stdout
    source = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "--dry-run",
            "-f",
            "docker/quality.mk",
            "source-identity",
            "CONTAINERIZED=0",
            "SHELL=/bin/false",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert source.returncode == 0, source.stderr
    assert 'bash docker/source_identity.sh "coverage/source.json"' in source.stdout
    assert "env -u" not in source.stdout


def test_ci_and_container_consume_reviewed_runtime_versions() -> None:
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    verify = jobs["verify"]["steps"]
    (setup_uv,) = (
        step for step in verify if step.get("uses", "").startswith("astral-sh/setup-uv@")
    )
    instructions = "\n".join(step.get("run", "") for step in verify)
    (python,) = re.findall(r"uv python install (\d+\.\d+\.\d+)\b", instructions)
    runtime = {"uv": setup_uv["with"]["version"], "python": python}
    assert re.fullmatch(r"\d+\.\d+\.\d+", runtime["uv"])
    for job in ("verify", "compatibility", "release-builder"):
        (setup,) = (
            s for s in jobs[job]["steps"] if s.get("uses", "").startswith("astral-sh/setup-uv@")
        )
        assert setup["with"]["version"] == runtime["uv"]
        assert re.fullmatch(r"astral-sh/setup-uv@[0-9a-f]{40}", setup["uses"])
        if job != "compatibility":
            shell = "\n".join(step.get("run", "") for step in jobs[job]["steps"])
            versions = re.findall(r"(?:uv python install |--python )(\d+\.\d+\.\d+)", shell)
            assert versions and set(versions) == {runtime["python"]}
    docker = (ROOT / "Dockerfile.quality").read_text()
    assert 'CMD ["make", "native-check"]' in docker
    for line in docker.splitlines():
        if line.startswith(("FROM python:", "FROM ghcr.io/astral-sh/uv:")):
            assert re.search(r"@sha256:[0-9a-f]{64} AS ", line)
    assert set(re.findall(r"^FROM python:([0-9.]+)-", docker, re.M)) == {runtime["python"]}
    assert set(re.findall(r"^FROM ghcr.io/astral-sh/uv:([0-9.]+)@", docker, re.M)) == {
        runtime["uv"]
    }


def test_release_target_outputs_are_normalized_from_project_metadata(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="Example.Demo"\nversion="1.2.3.dev4"\n'
    )
    output = subprocess.check_output(
        [sys.executable, "-I", ROOT / "docker/release_target.py"], cwd=tmp_path, text=True
    )
    assert output.splitlines() == ["package-name=example-demo", "version-target=1.2.3"]
