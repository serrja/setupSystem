"""Reject unsupported sources, privileged execution paths and arbitrary native task input."""

import json
import runpy
import subprocess
import tomllib
from pathlib import Path

import pytest
from ansible.errors import AnsibleFilterError

from setup_system.infrastructure.upstream_asset import github_deb

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a",
        "https://evil.invalid/path",
        "https://user:secret@github.com/a",
        "https://github.com:8443/a",
    ],
)
def test_privileged_sources_require_exact_release_url(url):
    entry = tomllib.loads((ROOT / "config/workspace/applications.toml").read_text())["happ"]
    asset = entry["asset"]["amd64"]
    upstream = {
        "draft": False,
        "prerelease": False,
        "published_at": "2026-01-01",
        "tag_name": "1.2",
        "assets": [{"name": asset, "browser_download_url": url, "digest": "sha256:" + "f" * 64}],
    }
    with pytest.raises(ValueError, match="SHA-256"):
        github_deb(upstream, entry, "amd64")


def test_unrecognized_role_is_rejected_before_dispatch(tmp_path):
    run = subprocess.run(
        ["/bin/sh", str(ROOT / "libexec/play.sh"), "base", "shell"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert run.returncode and "Unknown packaged operation" in run.stderr
    assert not list(tmp_path.iterdir())


def test_caller_cannot_inject_package_options_or_a_command(tmp_path):
    filters = runpy.run_path(str(ROOT / "ansible/filter_plugins/native.py"))[
        "FilterModule"
    ]().filters()
    apps = tomllib.loads((ROOT / "config/workspace/applications.toml").read_text())
    for settings in (
        {"apps": ["--help"]},
        {"apps": ["../pkg"]},
        {"apps": ["systemd"]},
        {"command": "id"},
    ):
        with pytest.raises((ValueError, AnsibleFilterError)):
            filters["native_request"](
                json.dumps({"schema": 1, "settings": settings, "expected": None}),
                "workspace",
                json.dumps(apps),
                "{}",
            )
    assert not list(tmp_path.iterdir())
