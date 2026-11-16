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
        "README.md",
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "uv.lock",
        "install.sh",
    ):
        shutil.copyfile(ROOT / name, source / name)
    for directory in (
        "src/setup_system",
        "assets",
        "manifests",
        "coverage/old-artifacts",
        ".venv",
        "src/setup_system/__pycache__",
    ):
        (source / directory).mkdir(parents=True)
    (source / "src/setup_system/__init__.py").write_text('"""Fixture."""\n')
    (source / "assets/fixture.txt").write_text("resource\n")
    (source / "manifests/fixture.toml").write_text('owner = "fixture"\n')
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
        assert "assets/fixture.txt" in names and "manifests/fixture.toml" in names
        assert not any(
            word in name
            for name in names
            for word in ("coverage", ".venv", "private", "__pycache__")
        )
