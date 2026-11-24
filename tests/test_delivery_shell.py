"""Public delivery commands refuse unsupported systems before changing any files."""

import os
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("uid", "release", "arch", "allowed"),
    [
        ("0", "debian:13:trixie", "amd64", False),
        ("1000", "ubuntu:26.04:resolute", "amd64", False),
        ("1000", "debian:12:bookworm", "amd64", False),
        ("1000", "debian:13:unknown", "arm64", False),
        ("1000", "debian:13:trixie", "i386", False),
        ("1000", "debian:13:trixie", "amd64", True),
        ("1000", "debian:13:trixie", "arm64", True),
    ],
)
def test_bootstrap_preflight_preserves_home(tmp_path, uid, release, arch, allowed):
    target = tmp_path / "os-release"
    distro, version, codename = release.split(":")
    target.write_text(f"ID={distro}\nVERSION_ID={version}\nVERSION_CODENAME={codename}\n")
    home = tmp_path / "synthetic HOME"
    home.mkdir(mode=0o700)
    contents = (
        (ROOT / "install.sh").read_text().replace("/etc/os-release", shlex.quote(str(target)))
    )
    helper = tmp_path / "install.sh"
    helper.write_text(contents)
    probe = '. ./install.sh; id() { echo "$USER_ID"; }; dpkg() { echo "$ARCH"; }; check_platform'
    result = subprocess.run(
        ["/bin/sh", "-c", probe, "install-local.sh"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(home), "USER_ID": uid, "ARCH": arch},
    )
    assert (result.returncode == 0) == allowed
    assert not list(home.iterdir())


def test_run_only_invokes_installed_launcher(tmp_path):
    sentinel = tmp_path / "called"
    launch = tmp_path / "installed"
    launch.write_text('#!/bin/sh\n[ "$#" = 0 ] || exit 2\nprintf run > "$CALLED"\n')
    launch.chmod(0o755)
    script = (
        (ROOT / "install.sh").read_text().replace("/usr/bin/setup-system", shlex.quote(str(launch)))
    )
    script = script.replace("$(id -u)", "$(echo 1000)")
    installer = tmp_path / "install.sh"
    installer.write_text(script)
    env = {**os.environ, "CALLED": str(sentinel), "HOME": str(tmp_path / "absent HOME")}
    subprocess.run(["sh", installer, "run"], check=True, env=env)
    assert sentinel.read_text() == "run" and not (tmp_path / "absent HOME").exists()
    for args in (["run", "--release", "v1.2.3"], ["install", "--unsupported"], ["--as-root"]):
        assert (
            subprocess.run(
                ["sh", installer, *args], env=env, capture_output=True, check=False
            ).returncode
            == 2
        )
