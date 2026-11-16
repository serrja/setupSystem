"""Native H02 builds run in an export, never in the original checkout."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
PROVIDER = "e5cac33af228224af9deaef308e39c1b9f788033"


@pytest.fixture
def helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "native_release", ROOT / "docker/python_release.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in list(os.environ):
        if key.startswith(("GITHUB_", "RELEASE_")):
            monkeypatch.delenv(key)
    root = tmp_path / "source"
    root.mkdir()
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    (root / "pyproject.toml").write_text(
        '[build-system]\nrequires=["hatchling>=1.27,<2"]\n'
        'build-backend="hatchling.build"\n'
        '[project]\nname="release-fixture"\nversion="1.2.3.dev1"\n'
        'requires-python=">=3.12"\n'
    )
    (root / "release_fixture").mkdir()
    (root / "release_fixture/__init__.py").write_text('VALUE = "installed"\n')
    (root / "uv.lock").write_text("# preserved original lock\nversion = 1\n")
    git(root, "init", "--quiet")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=f@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "fixture",
    )
    monkeypatch.chdir(root)
    return root


def allocation(root: Path, branch: str = "dev") -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "repository": "example/release-fixture",
        "adapter": "python",
        "bundleName": "release-fixture",
        "workflow": ".github/workflows/ci.yml",
        "gate": "required",
        "runId": "123",
        "branch": branch,
        "sha": git(root, "rev-parse", "HEAD"),
        "eventCreatedAt": "2026-11-15T00:00:00Z",
        "version": "1.2.3-dev.4" if branch == "dev" else "1.2.3",
        "providerRef": PROVIDER,
        "contractSchema": "h02-python/v1",
        "builderJob": "native build",
        "versionTarget": "1.2.3",
        "wheelTags": ["py3-none-any"],
        "allocationAttempt": "1",
        "pythonVersion": "1.2.3.dev4" if branch == "dev" else "1.2.3",
    }


def write_allocation(tmp_path: Path, data: dict[str, object]) -> tuple[Path, str]:
    path = tmp_path / "allocation.json"
    raw = json.dumps(data, separators=(",", ":")).encode()
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def build_args(tmp_path: Path, project: Path, data: dict[str, object]) -> list[str]:
    path, digest = write_allocation(tmp_path, data)
    return [
        "build",
        "--allocation",
        str(path),
        "--allocation-sha256",
        digest,
        "--provider-ref",
        PROVIDER,
        "--destination",
        str(tmp_path / "stage"),
        "--output",
        str(tmp_path / "dist"),
        "--evidence",
        str(tmp_path / "build.json"),
        "--source-pyproject",
        str(project / "pyproject.toml"),
    ]


@pytest.mark.parametrize("branch", ["dev", "main", "advanced-floor"])
def test_real_native_build_and_outside_installed_metadata(
    tmp_path: Path, project: Path, helper: ModuleType, branch: str
) -> None:
    original = {name: (project / name).read_bytes() for name in ("pyproject.toml", "uv.lock")}
    data = allocation(project, "dev" if branch == "advanced-floor" else branch)
    if branch == "advanced-floor":
        data.update(version="1.2.4-dev.4", pythonVersion="1.2.4.dev4")
    result = CliRunner().invoke(helper.app, build_args(tmp_path, project, data))
    assert result.exit_code == 0, result.output + str(result.exception)
    dist = tmp_path / "dist"
    assert sorted(path.suffix for path in dist.iterdir()) == [".gz", ".whl"]
    evidence = json.loads((tmp_path / "build.json").read_text())
    assert evidence["buildTool"]["backend"] == "hatchling.build"
    assert evidence["buildTool"]["backendVersion"]
    assert evidence["buildTool"]["frontend"] == "uv"
    assert evidence["buildTool"]["versionInjection"] == "uv version --frozen --offline"
    assert original == {name: (project / name).read_bytes() for name in original}
    assert (tmp_path / "stage/uv.lock").read_bytes() == original["uv.lock"]
    assert git(project, "status", "--porcelain") == ""
    metadata = []
    for path in sorted(dist.iterdir()):
        item = {
            "name": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "packageName": "release-fixture",
            "version": data["pythonVersion"],
            "requiresPython": ">=3.12",
        }
        if path.suffix == ".whl":
            item["tags"] = ["py3-none-any"]
        else:
            item["buildBackend"] = "hatchling.build"
        metadata.append(item)
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps(metadata))
    allocation_path, checksum = write_allocation(tmp_path, data)
    receipt_args = [
        "receipt",
        "--allocation",
        str(allocation_path),
        "--allocation-sha256",
        checksum,
        "--metadata",
        str(metadata_path),
        "--build-evidence",
        str(tmp_path / "build.json"),
        "--job-id",
        "456",
        "--run-attempt",
        "1",
        "--output",
        str(dist / "python-build-receipt.json"),
    ]
    invalid_attempt = receipt_args.copy()
    invalid_attempt[invalid_attempt.index("--run-attempt") + 1] = "2"
    assert CliRunner().invoke(helper.app, invalid_attempt).exit_code != 0
    metadata[0]["sha256"] = "0" * 64
    metadata_path.write_text(json.dumps(metadata))
    assert CliRunner().invoke(helper.app, receipt_args).exit_code != 0
    metadata[0]["sha256"] = hashlib.sha256(
        (dist / str(metadata[0]["name"])).read_bytes()
    ).hexdigest()
    metadata_path.write_text(json.dumps(metadata))
    wheel = next(dist.glob("*.whl"))
    original_wheel = wheel.read_bytes()
    wheel.write_bytes(original_wheel + b"stale")
    assert CliRunner().invoke(helper.app, receipt_args).exit_code != 0
    wheel.write_bytes(original_wheel)
    result = CliRunner().invoke(helper.app, receipt_args)
    assert result.exit_code == 0, result.output + str(result.exception)
    receipt = json.loads((dist / "python-build-receipt.json").read_text())
    assert set(receipt) == {
        "schemaVersion",
        "allocationSha256",
        "runId",
        "runAttempt",
        "sourceSha",
        "builderJob",
        "builderJobId",
        "buildTool",
        "files",
    }
    assert receipt["files"] == metadata
    assert receipt["builderJobId"] == "456"
    assert CliRunner().invoke(helper.app, receipt_args).exit_code != 0
    installed = tmp_path / "installed"
    subprocess.run(
        ["uv", "venv", "--offline", "--python", sys.executable, str(installed)],
        check=True,
    )
    installed_python = installed / "bin/python"
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--offline",
            "--no-deps",
            "--python",
            str(installed_python),
            str(next(dist.glob("*.whl"))),
        ],
        check=True,
    )
    probe = subprocess.check_output(
        [
            str(installed_python),
            "-I",
            "-c",
            "import importlib.metadata as m;"
            "import release_fixture;print(m.version('release-fixture'),"
            "release_fixture.VALUE)",
        ],
        cwd=tmp_path,
        text=True,
    )
    assert probe.strip() == f"{data['pythonVersion']} installed"


@pytest.mark.parametrize(
    "change",
    [
        {"schemaVersion": True},
        {"providerRef": "a" * 40},
        {"sha": "b" * 40},
        {"pythonVersion": "1.2.3"},
        {"wheelTags": ["py3-none-any", "py3-none-any"]},
        {"extra": "forbidden"},
        {"allocationAttempt": "0"},
        {"eventCreatedAt": "invalid"},
    ],
)
def test_reject_allocation_before_export(
    tmp_path: Path, project: Path, helper: ModuleType, change: dict[str, object]
) -> None:
    result = CliRunner().invoke(
        helper.app, build_args(tmp_path, project, allocation(project) | change)
    )
    assert result.exit_code != 0
    assert not (tmp_path / "stage").exists()


def test_reject_digest_dirty_identity_and_inside_export(
    tmp_path: Path, project: Path, helper: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = build_args(tmp_path, project, allocation(project))
    wrong = args.copy()
    wrong[wrong.index("--allocation-sha256") + 1] = "0" * 64
    assert CliRunner().invoke(helper.app, wrong).exit_code != 0
    monkeypatch.setenv("GITHUB_REPOSITORY", "other/repository")
    assert CliRunner().invoke(helper.app, args).exit_code != 0
    monkeypatch.delenv("GITHUB_REPOSITORY")
    unsafe = args.copy()
    unsafe[unsafe.index("--destination") + 1] = str(project / "stage")
    assert CliRunner().invoke(helper.app, unsafe).exit_code != 0
    (project / "untracked").write_text("owner bytes")
    assert CliRunner().invoke(helper.app, args).exit_code != 0
    assert not (tmp_path / "stage").exists()


def test_git_export_rejects_symlink(tmp_path: Path, project: Path, helper: ModuleType) -> None:
    (project / "link").symlink_to("pyproject.toml")
    git(project, "add", "link")
    git(
        project,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=f@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "link",
    )
    result = CliRunner().invoke(helper.app, build_args(tmp_path, project, allocation(project)))
    assert result.exit_code != 0
    assert not (tmp_path / "stage").exists()


def test_backend_environment_excludes_credentials(helper: ModuleType) -> None:
    env = helper.backend_environment(
        {
            "PATH": "/bin",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "synthetic",
            "GH_TOKEN": "synthetic",
            "UV_INDEX_PASSWORD": "synthetic",
            "GIT_CONFIG_COUNT": "1",
            "SSH_AUTH_SOCK": "/tmp/socket",
            "PYTHONPATH": "/outside",
            "UV_CACHE_DIR": "/cache",
            "PIP_INDEX_URL": "https://user:synthetic@example.invalid/simple",
            "PIP_EXTRA_INDEX_URL": "https://user:synthetic@example.invalid/simple",
            "UV_DEFAULT_INDEX": "https://user:synthetic@example.invalid/simple",
            "PIP_CONFIG_FILE": "/private/config",
            "UV_CONFIG_FILE": "/private/config",
            "GIT_ASKPASS": "/private/helper",
            "SSH_ASKPASS": "/private/helper",
            "DATABASE_URL": "synthetic-credential-bearing-value",
            "CUSTOM_API_KEY": "synthetic",
        }
    )
    assert env == {"PATH": "/bin", "UV_CACHE_DIR": "/cache"}


@pytest.mark.parametrize(
    "original,replacement",
    [
        ('version="1.2.3.dev1"', 'dynamic=["version"]'),
        ('build-backend="hatchling.build"', ""),
        ('name="release-fixture"', 'name="other-package"'),
        ('version="1.2.3.dev1"', 'version="2.0.0"'),
    ],
)
def test_reject_unsupported_source_metadata(
    tmp_path: Path, project: Path, helper: ModuleType, original: str, replacement: str
) -> None:
    path = project / "pyproject.toml"
    path.write_text(path.read_text().replace(original, replacement))
    git(project, "add", ".")
    git(
        project,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=f@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "metadata",
    )
    result = CliRunner().invoke(helper.app, build_args(tmp_path, project, allocation(project)))
    assert result.exit_code != 0
    assert not (tmp_path / "stage").exists()


def test_git_export_rejects_case_collision(
    tmp_path: Path, project: Path, helper: ModuleType
) -> None:
    (project / "FILE").write_text("upper")
    (project / "file").write_text("lower")
    git(project, "add", ".")
    git(
        project,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=f@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "case collision",
    )
    assert (
        CliRunner().invoke(helper.app, build_args(tmp_path, project, allocation(project))).exit_code
        != 0
    )
    assert not (tmp_path / "stage").exists()


@pytest.mark.parametrize("limit", ["MAX_ARCHIVE", "MAX_MEMBER", "MAX_MEMBERS"])
def test_git_export_limits_before_extracting(
    tmp_path: Path,
    project: Path,
    helper: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    limit: str,
) -> None:
    monkeypatch.setattr(helper, limit, 1)
    result = CliRunner().invoke(helper.app, build_args(tmp_path, project, allocation(project)))
    assert result.exit_code != 0
    assert not (tmp_path / "stage").exists()


@pytest.mark.parametrize("phase", ["version", "build"])
def test_failed_native_process_keeps_source_and_recovers_at_fresh_paths(
    tmp_path: Path,
    project: Path,
    helper: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    original = {name: (project / name).read_bytes() for name in ("pyproject.toml", "uv.lock")}
    head = git(project, "rev-parse", "HEAD")
    args = build_args(tmp_path, project, allocation(project))
    native_run = subprocess.run

    def fail_native(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if command[:2] == ["uv", phase]:
            raise subprocess.CalledProcessError(9, command)
        return native_run(command, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(helper.subprocess, "run", fail_native)
        result = CliRunner().invoke(helper.app, args)
        assert result.exit_code != 0
    assert not (tmp_path / "build.json").exists()
    assert original == {name: (project / name).read_bytes() for name in original}
    assert git(project, "rev-parse", "HEAD") == head
    assert git(project, "status", "--porcelain") == ""
    stage = tmp_path / "stage"
    saved = {
        str(path.relative_to(stage)): path.read_bytes()
        for path in stage.rglob("*")
        if path.is_file()
    }
    assert CliRunner().invoke(helper.app, args).exit_code != 0
    assert saved == {
        str(path.relative_to(stage)): path.read_bytes()
        for path in stage.rglob("*")
        if path.is_file()
    }
    for option, name in [
        ("--destination", "retry-stage"),
        ("--output", "retry-dist"),
        ("--evidence", "retry-build.json"),
    ]:
        args[args.index(option) + 1] = str(tmp_path / name)
    result = CliRunner().invoke(helper.app, args)
    assert result.exit_code == 0, result.output + str(result.exception)
    assert original == {name: (project / name).read_bytes() for name in original}
    assert git(project, "status", "--porcelain") == ""
