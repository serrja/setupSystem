"""Local Make delivery accepts a single regular, explicitly prepared Debian package."""

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("case", ["empty", "ambiguous", "symlink"])
def test_local_package_selection_stops_before_sudo(tmp_path: Path, case: str) -> None:
    (tmp_path / "debian").mkdir()
    shutil.copyfile(ROOT / "debian/install-local", tmp_path / "debian/install-local")
    (tmp_path / "mk").mkdir()
    shutil.copyfile(ROOT / "mk/package.mk", tmp_path / "mk/package.mk")
    release = tmp_path / "os-release"
    release.write_text("ID=debian\nVERSION_ID=13\nVERSION_CODENAME=trixie\n")
    tools = tmp_path / "tools"
    tools.mkdir()
    for name, body in {
        "id": "echo 1000",
        "dpkg": 'test "$1" = --print-architecture && echo amd64',
        "sudo": 'touch "$CALLED"; exit 89',
    }.items():
        script = tools / name
        script.write_text("#!/bin/sh\n" + body + "\n")
        script.chmod(0o755)
    source = (ROOT / "install.sh").read_text().replace("/etc/os-release", shlex.quote(str(release)))
    source = source.replace(
        "PATH=/usr/sbin:/usr/bin:/sbin:/bin", f"PATH={shlex.quote(str(tools))}:/usr/bin:/bin"
    )
    (tmp_path / "install.sh").write_text(source)
    output = tmp_path / "prepared packages"
    output.mkdir()
    personal = tmp_path / "owner file"
    personal.write_text("owner data")
    if case == "symlink":
        (output / "candidate.deb").symlink_to(personal)
    elif case == "ambiguous":
        (output / "first.deb").write_bytes(b"first")
        (output / "second.deb").write_bytes(b"second")
    called = tmp_path / "privilege-requested"
    result = subprocess.run(
        ["make", "-rR", "-f", "mk/package.mk", "install", f"PACKAGE_DIR={output}"],
        cwd=tmp_path,
        env={**os.environ, "CALLED": str(called)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0 and "exactly one regular .deb" in result.stderr
    assert not called.exists() and personal.read_text() == "owner data"
