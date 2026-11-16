"""Validate locked runtime preparation from allocated source distributions."""

from __future__ import annotations

import copy
import hashlib
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from setup_system.application.installer import InstallationService, InstallRequest, Operation
from setup_system.runtime import UserRuntime

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "released source"
    root.mkdir()
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(ROOT / name, root / name)
    for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        (root / name).write_text("Fixture build input.\n")
    for name in ("src/setup_system", "assets", "manifests"):
        (root / name).mkdir(parents=True)
    (root / "src/setup_system/__init__.py").write_text('"""Fixture package."""\n')
    (root / "assets/fixture.txt").write_text("asset\n")
    (root / "manifests/fixture.toml").write_text('owner = "fixture"\n')
    (root / "private-untracked.txt").write_text("must stay outside build staging\n")
    (root / ".git").mkdir()
    (root / ".git/config").write_text("must not enter build staging\n")
    return root


def allocate(root: Path, version: str) -> None:
    path = root / "pyproject.toml"
    original = path.read_text()
    project = tomllib.loads(original)["project"]
    old = 'version = "' + project["version"] + '"'
    assert original.count(old) == 1
    path.write_text(original.replace(old, 'version = "' + version + '"'))


def hashes(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file() and not p.is_symlink()
    }


def lock_check(project: Path, cache: Path) -> subprocess.CompletedProcess[str]:
    uv = shutil.which("uv")
    assert uv is not None, "The documented pinned uv test tool must be available"
    version = subprocess.run([uv, "--version"], capture_output=True, text=True, timeout=10)
    assert version.returncode == 0 and version.stdout.split()[:2] == ["uv", "0.12.15"]
    return subprocess.run(
        [
            uv,
            "lock",
            "--check",
            "--offline",
            "--project",
            str(project),
            "--python",
            sys.executable,
        ],
        env={**os.environ, "UV_CACHE_DIR": str(cache), "UV_OFFLINE": "true"},
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_matching_source_reuses_original_without_staging(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = UserRuntime(source)
    cache = tmp_path / "cache"
    cache.mkdir()
    before = hashes(source)
    with runtime.project(cache) as project:
        assert project == source
        assert not list(cache.iterdir())
    assert hashes(source) == before


@pytest.mark.parametrize("version", ["0.3.0", "0.3.1.dev7"])
def test_allocated_source_only_reconciles_root_version_and_keeps_lock_valid(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    runtime = UserRuntime(source)
    baseline = lock_check(source, tmp_path / "baseline uv cache")
    assert baseline.returncode == 0, baseline.stderr
    allocate(source, version)
    before = hashes(source)
    rejected = lock_check(source, tmp_path / "original uv cache")
    assert rejected.returncode != 0 and rejected.stderr
    original = tomllib.loads((source / "uv.lock").read_text())
    cache = tmp_path / "private cache"
    cache.mkdir()
    with runtime.project(cache) as project:
        assert project != source and project.is_relative_to(cache)
        assert project.stat().st_mode & 0o077 == 0
        assert not (project / ".git").exists()
        assert not (project / "private-untracked.txt").exists()
        for name in ("pyproject.toml", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
            assert (project / name).read_bytes() == (source / name).read_bytes()
        for name in (
            "src/setup_system/__init__.py",
            "assets/fixture.txt",
            "manifests/fixture.toml",
        ):
            assert (project / name).read_bytes() == (source / name).read_bytes()
        expected = copy.deepcopy(original)
        roots = [p for p in expected["package"] if p["name"] == "setup-system"]
        assert len(roots) == 1
        roots[0]["version"] = version
        assert tomllib.loads((project / "uv.lock").read_text()) == expected
        accepted = lock_check(project, tmp_path / "normalized uv cache")
        assert accepted.returncode == 0, accepted.stderr
    assert not project.exists()
    assert hashes(source) == before


def test_reconciled_release_still_rejects_dependency_drift(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = UserRuntime(source)
    allocate(source, "0.3.0")
    path = source / "pyproject.toml"
    path.write_text(path.read_text().replace("typer>=0.27.2,<1", "typer==0.1.0"))
    assert "typer==0.1.0" in path.read_text()
    before = hashes(source)
    cache = tmp_path / "private cache"
    cache.mkdir()
    with runtime.project(cache) as project:
        result = lock_check(project, tmp_path / "drift uv cache")
        assert result.returncode != 0
    assert not project.exists()
    assert hashes(source) == before


def test_private_stage_is_removed_after_user_stage_failure(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = UserRuntime(source)
    allocate(source, "0.3.0")
    cache = tmp_path / "private cache"
    cache.mkdir()
    before = hashes(source)
    with pytest.raises(RuntimeError, match="user stage failed"):
        with runtime.project(cache) as project:
            assert project.exists()
            raise RuntimeError("user stage failed")
    assert not project.exists() and not list(cache.iterdir())
    assert hashes(source) == before


@pytest.mark.parametrize("kind", ["symlink", "fifo", "entry-limit", "size-limit", "metadata-type"])
def test_unsafe_or_excessive_release_build_inputs_are_rejected(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    runtime = UserRuntime(source)
    allocate(source, "0.3.0")
    if kind == "symlink":
        secret = tmp_path / "outside-source.txt"
        secret.write_text("outside source\n")
        (source / "assets/unsafe").symlink_to(secret)
    elif kind == "fifo":
        os.mkfifo(source / "assets/unsafe")
    elif kind == "entry-limit":
        for index in range(4097):
            (source / "assets" / str(index)).touch()
    elif kind == "metadata-type":
        (source / "README.md").unlink()
        (source / "README.md").mkdir()
    else:
        with (source / "assets/too-large").open("wb") as f:
            f.truncate(33 * 1024 * 1024)
    cache = tmp_path / "private cache"
    cache.mkdir()
    with pytest.raises((ValueError, RuntimeError)):
        with runtime.project(cache):
            pytest.fail("Unsafe build input was accepted")
    assert not list(cache.iterdir())


def test_privileged_runtime_source_preparation_is_rejected_before_writes(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = UserRuntime(source)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    cache = tmp_path / "never-created cache"
    before = hashes(source)
    with pytest.raises((ValueError, RuntimeError)):
        with runtime.project(cache):
            pytest.fail("Root reached runtime source preparation")
    assert not cache.exists() and hashes(source) == before


@pytest.mark.parametrize("kind", ["missing-root", "duplicate-root", "foreign-source", "version"])
def test_malformed_local_project_identity_is_rejected_without_staging(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    runtime = UserRuntime(source)
    allocate(source, "0.3.0" if kind != "version" else "../not-a-version")
    path = source / "uv.lock"
    data = path.read_text()
    if kind == "missing-root":
        data = data.replace('name = "setup-system"', 'name = "other-project"')
    elif kind == "duplicate-root":
        data += '\n[[package]]\nname = "setup-system"\nversion = "0.3.0.dev0"\n'
        data += 'source = { editable = "." }\n'
    elif kind == "foreign-source":
        data = data.replace('source = { editable = "." }', 'source = { path = "../outside" }')
    path.write_text(data)
    before = hashes(source)
    cache = tmp_path / "never-created cache"
    with pytest.raises((ValueError, RuntimeError)):
        with runtime.project(cache):
            pytest.fail("Malformed local project identity was accepted")
    assert not cache.exists() and hashes(source) == before


def test_readonly_source_lock_is_normalized_privately_without_changing_input(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = UserRuntime(source)
    allocate(source, "0.3.0")
    lock = source / "uv.lock"
    lock.chmod(0o444)
    before = hashes(source)
    cache = tmp_path / "private cache"
    cache.mkdir()
    with runtime.project(cache) as project:
        assert project != source
        result = lock_check(project, tmp_path / "readonly source uv cache")
        assert result.returncode == 0, result.stderr
    assert not project.exists()
    assert lock.stat().st_mode & 0o777 == 0o444
    assert hashes(source) == before


@pytest.mark.parametrize("kind", ["missing-license", "assets-symlink"])
def test_invalid_allocated_build_inputs_fail_before_system_apply(
    source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: str,
) -> None:
    from setup_system import installation as install

    runtime = UserRuntime(source)
    allocate(source, "0.3.0")
    for manifest in ("platforms.toml", "network.toml", "happ.toml"):
        (source / "manifests" / manifest).write_text('owner = "fixture"\n')
    (source / "src/setup_system/application").mkdir()
    for filename in (
        "_installer_entry.py",
        "runtime.py",
        "application/installer.py",
        "network_worker.py",
    ):
        (source / "src/setup_system" / filename).write_text('"""Fixture source."""\n')
    (source / "assets/hyprland").mkdir()
    (source / "assets/hyprland/profile.lua").write_text("-- Fixture profile.\n")
    if kind == "missing-license":
        (source / "LICENSE").unlink()
    else:
        outside = tmp_path / "external assets"
        (source / "assets").rename(outside)
        (source / "assets").symlink_to(outside, target_is_directory=True)
    home = tmp_path / "target home"
    home.mkdir()
    target = install.TargetUser("alice", os.getuid(), os.getgid(), home, "/bin/bash")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(install, "current_packages", lambda root: ["hyprland=fixture"])
    monkeypatch.setattr(install, "resolve_target", lambda name: target)
    monkeypatch.setattr(install, "validate_installation_paths", lambda *args: None)
    monkeypatch.setattr(
        install,
        "prepare_packages",
        lambda *args, **kwargs: pytest.fail("APT reached invalid bundle"),
    )
    monkeypatch.setattr(
        install,
        "run_command",
        lambda *args, **kwargs: pytest.fail("User runtime reached invalid bundle"),
    )
    before = hashes(source)
    assert (
        InstallationService(source, runtime=runtime).run(
            InstallRequest(Operation.APPLY, user="alice", yes=True)
        )
        == 2
    )
    assert "Preflight failed:" in capsys.readouterr().err
    assert hashes(source) == before and not list(home.iterdir())


@pytest.mark.parametrize(
    "case",
    [
        "ready",
        "missing-runtime",
        "missing-receipt",
        "changed-receipt",
        "not-executable",
        "symlink",
        "root",
    ],
)
def test_prepared_runtime_handoff_is_read_only_and_rejects_unsafe_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    from setup_system import installation

    home = tmp_path / "prepared desktop home"
    runtime = home / "data/setup-system/runtime"
    (runtime / "bin").mkdir(parents=True)
    command = runtime / "bin/setup-system"
    command.write_text("#!/bin/sh\nexit 0\n")
    command.chmod(0o700)
    receipt = runtime.parent / "runtime.json"
    receipt.write_bytes(b'{"owner":"setupSystem","runtimePython":"3.14.7"}\n')
    target = installation.TargetUser("runtime_fixture", os.getuid(), os.getgid(), home, "/bin/bash")
    monkeypatch.setattr(installation, "resolve_target", lambda _: target)
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "data"))
    monkeypatch.setattr(
        installation,
        "run_command",
        lambda *a, **kw: pytest.fail("Offline handoff executed a command"),
    )
    if case == "missing-runtime":
        command.unlink()
        command.parent.rmdir()
        runtime.rmdir()
    elif case == "missing-receipt":
        receipt.unlink()
    elif case == "changed-receipt":
        receipt.write_text("Unmanaged user data; preserve this file")
    elif case == "not-executable":
        command.chmod(0o600)
    elif case == "symlink":
        original = runtime.parent / "external-command"
        command.rename(original)
        command.symlink_to(original)
    elif case == "root":
        monkeypatch.setattr(os, "geteuid", lambda: 0)
    before = hashes(home)
    if case == "ready":
        assert UserRuntime(ROOT).existing() == runtime
        assert UserRuntime(ROOT).existing() == runtime
    else:
        with pytest.raises((ValueError, RuntimeError)):
            UserRuntime(ROOT).existing()
    assert hashes(home) == before
