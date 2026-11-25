"""Source releases contain finite product inputs, never local caches or reports."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_real_sdist_excludes_untracked_local_material(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in (
        "pyproject.toml",
        "hatch_build.py",
        "README.md",
        "LICENSE.md",
        "THIRD_PARTY_NOTICES.md",
        "uv.lock",
        "install.sh",
        "Makefile",
        "ansible.cfg",
    ):
        shutil.copyfile(ROOT / name, source / name)
    (source / "docker").mkdir()
    shutil.copyfile(ROOT / "docker/plugin_artifacts.py", source / "docker/plugin_artifacts.py")
    shutil.copytree(
        ROOT / "plugins/applications/git",
        source / "plugins/applications/git",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for directory in (
        "src/setup_system",
        "profiles",
        "config",
        "libexec",
        "mk",
        "debian",
        "docs",
        "coverage/old-artifacts",
        ".venv",
        "src/setup_system/__pycache__",
    ):
        (source / directory).mkdir(parents=True)
    (source / "src/setup_system/__init__.py").write_text('"""Fixture."""\n')
    (source / "profiles/fixture.toml").write_text("resource\n")
    (source / "config/fixture.toml").write_text('owner = "fixture"\n')
    for folder in ("mk", "libexec", "debian", "docs"):
        (source / folder / "fixture").write_text("public payload\n")
    for name in (
        "coverage/old-artifacts/private.tar.gz",
        ".venv/private",
        "untracked-private.txt",
        "src/setup_system/__pycache__/private.pyc",
    ):
        (source / name).write_bytes(b"SYNTHETIC_PRIVATE_NOT_FOR_DISTRIBUTION")
    output = tmp_path / "output"
    output.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import hatchling.build,sys; hatchling.build.build_sdist(sys.argv[1])",
            str(output),
        ],
        cwd=source,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    archive = next(output.glob("*.tar.gz"))
    assert archive.stat().st_size < 1024 * 1024
    with tarfile.open(archive) as bundle:
        names = [member.name.partition("/")[2] for member in bundle.getmembers() if member.isfile()]
        assert "install.sh" in names and "src/setup_system/__init__.py" in names
        assert "profiles/fixture.toml" in names and "config/fixture.toml" in names
        assert {"Makefile", "ansible.cfg"} <= set(names)
        assert all(folder + "/fixture" in names for folder in ("mk", "libexec", "debian", "docs"))
        assert not any(
            word in name
            for name in names
            for word in ("coverage", ".venv", "private", "__pycache__")
        )


def test_release_builder_publishes_matching_wheel_and_no_partial_bad_identity(tmp_path):
    import hashlib
    import json
    import runpy

    import pytest

    from setup_system.extensions import Release, wheel_manifest

    build = runpy.run_path(str(ROOT / "docker/plugin_artifacts.py"))["build"]
    owner = tmp_path / "plugins/applications/git"
    shutil.copytree(
        ROOT / "plugins/applications/git",
        owner,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"),
    )
    output = tmp_path / "catalog"
    paths = build(tmp_path, output, Path(sys.executable))
    release = Release((output / "git.json").read_bytes()).selected()
    (record,) = release.wheels
    data = (output / record.name).read_bytes()
    assert {p.name for p in paths} == {"git.json", record.name}
    assert record.sha256 == hashlib.sha256(data).hexdigest()
    assert release.manifest == wheel_manifest(record.name, data)
    old = {p.name: p.read_bytes() for p in output.iterdir()}
    metadata = owner / "src/setup_plugin_git/manifest.json"
    broken = json.loads(metadata.read_bytes())
    broken["id"] = "unexpected"
    metadata.write_text(json.dumps(broken))
    with pytest.raises(ValueError, match="no longer matches"):
        build(tmp_path, output, Path(sys.executable))
    assert old == {p.name: p.read_bytes() for p in output.iterdir()}


def test_runtime_builder_uses_locked_host_hash_and_current_platform_only(tmp_path, monkeypatch):
    import hashlib
    import runpy

    import pytest
    from test_extensions import wheel

    from setup_system.infrastructure import downloads

    builder = runpy.run_path(str(ROOT / "docker/plugin_artifacts.py"))["runtime_wheels"]
    payload = wheel(tmp_path, "dep", "1.0", "dep", "").read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    name = "dep-1.0-py3-none-any.whl"
    url = "https://files.pythonhosted.org/fixture/" + name
    header = 'name="dep"\nversion="1.0"\nindex="https://pypi.org/simple"\n'
    row = f'[[packages.wheels]]\nurl="{url}"\nsize={len(payload)}\nhashes={{sha256="{digest}"}}'
    lock = ("[[packages]]\n" + header + row).encode()

    def core(_source, runtime, _python):
        return wheel(runtime, "fixture-core", "1.0", "fixture_core", "")

    monkeypatch.setitem(builder.__globals__, "build_wheel", core)
    monkeypatch.setattr(subprocess, "check_output", lambda *_: lock)
    received = []

    async def fetch(location, policy):
        received.append((location, policy))
        return payload

    monkeypatch.setattr(downloads, "download_https", fetch)
    dest = tmp_path / "released"
    stage = tmp_path / "stage"
    stage.mkdir()
    base, full = builder(tmp_path, stage, dest, Path(sys.executable))
    assert base == full and [r["name"] for r in full] == ["fixture_core-1.0-py3-none-any.whl", name]
    assert len(received) == 1 and received[0][0] == url
    assert (stage / "runtime" / name).read_bytes() == payload
    dest.mkdir()
    (stage / "runtime").rename(dest / "runtime")
    assert builder(tmp_path, stage, dest, Path(sys.executable)) == (base, full)
    assert len(received) == 1
    for i, (before, after, reason) in enumerate(
        (
            (b"pypi.org", b"foreign.invalid", "package index"),
            (b"files.pythonhosted.org", b"foreign.invalid", "wheel origin"),
            (b"py3-none-any", b"cp37-cp37-win32", "compatible binary wheel"),
            (b"dep-1.0-", b"dep-2.0-", "disagrees"),
        )
    ):
        case = tmp_path / str(i)
        case.mkdir()
        bad_lock = lock.replace(before, after)
        monkeypatch.setattr(subprocess, "check_output", lambda *_, data=bad_lock: data)
        with pytest.raises(ValueError, match=reason):
            builder(tmp_path, case, dest, Path(sys.executable))


def test_installed_wheel_verifier_checks_distribution_origin_and_resource_bytes(tmp_path):
    import hashlib
    import json
    import runpy
    from importlib.metadata import Distribution
    from zipfile import ZipFile

    import pytest
    from test_extensions import wheel

    archive = wheel(tmp_path, "isolated", "1.0", "isolated", "VALUE=1")
    install = tmp_path / "install"
    subprocess.run(
        [
            "uv",
            "--offline",
            "pip",
            "install",
            "--no-deps",
            "--no-index",
            "--target",
            str(install),
            str(archive),
        ],
        check=True,
    )
    dist = Distribution.at(install / "isolated-1.0.dist-info")
    verify = runpy.run_path(str(ROOT / "docker/verify-installed.py"))["verify_origin"]
    verify(dist, archive)
    origin = install / "isolated-1.0.dist-info/direct_url.json"
    original = json.loads(origin.read_bytes())
    origin.write_text(json.dumps(original | {"dir_info": {"editable": True}}))
    with pytest.raises(ValueError, match="independently installed"):
        verify(dist, archive)
    origin.write_text(json.dumps(original | {"url": "file:///unknown"}))
    with pytest.raises(ValueError, match="wheel origin"):
        verify(dist, archive)
    origin.write_text(json.dumps(original | {"archive_info": {"hashes": {"sha256": "0" * 64}}}))
    with pytest.raises(ValueError, match="digest"):
        verify(dist, archive)
    origin.write_text(
        json.dumps(
            original
            | {
                "archive_info": {
                    "hashes": {"sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
                }
            }
        )
    )
    target = install / "isolated/__init__.py"
    target.rename(target.with_suffix(".saved"))
    target.symlink_to(target.with_suffix(".saved"))
    with pytest.raises(ValueError, match="resource differs"):
        verify(dist, archive)
    target.unlink()
    target.with_suffix(".saved").rename(target)
    with ZipFile(archive, "a") as unsafe:
        unsafe.writestr("../outside", b"")
    origin.write_text(json.dumps(original | {"archive_info": {}}))
    with pytest.raises(ValueError, match="Unexpected installed"):
        verify(dist, archive)


def test_hatch_build_hook_keeps_editable_and_staged_release_lifecycles_separate(
    tmp_path, monkeypatch
):
    from importlib import import_module

    monkeypatch.syspath_prepend(str(ROOT))
    hook_type = import_module("hatch_build").PluginCatalogHook
    source = tmp_path / "source"
    shutil.copytree(
        ROOT / "plugins/applications/git",
        source / "plugins/applications/git",
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"),
    )
    (source / "docker").mkdir()
    shutil.copyfile(ROOT / "docker/plugin_artifacts.py", source / "docker/plugin_artifacts.py")
    for target, destination in (
        ("wheel", "setup_system/bundle/plugin_catalog"),
        ("sdist", "plugin_catalog"),
    ):
        hook = hook_type(str(source), {}, None, None, str(tmp_path), target)
        data = {"force_include": {}}
        hook.initialize("editable", data)
        hook.finalize("editable", data, "")
        assert not data["force_include"]
        hook.initialize("standard", data)
        [(stage_name, actual)] = data["force_include"].items()
        assert actual == destination
        stage = Path(stage_name)
        assert (stage / "git.json").is_file() and len(list(stage.glob("*.whl"))) == 1
        hook.finalize("standard", data, "")
        assert not stage.exists()


def test_smoke_navigation_and_managed_recovery_in_isolated_home(tmp_path, monkeypatch):
    import asyncio
    import runpy

    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    for scope in ("config", "cache", "data", "state"):
        monkeypatch.setenv("XDG_" + scope.upper() + "_HOME", str(home / scope))
    smoke = runpy.run_path(str(ROOT / "docker/installed_smoke.py"))
    asyncio.run(smoke["main"]())
    assert not (home / "config with spaces/example.ini").exists()
