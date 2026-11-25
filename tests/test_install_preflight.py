"""Reject unsupported sources, privileged execution paths and arbitrary native task input."""

import subprocess
import sys
from pathlib import Path

import pytest

from setup_system_sdk.apt import Approval, GithubRelease, Packages

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
    asset = "fixture_amd64.deb"
    upstream = {
        "draft": False,
        "prerelease": False,
        "published_at": "2026-01-01",
        "tag_name": "1.2",
        "assets": [{"name": asset, "browser_download_url": url, "digest": "sha256:" + "f" * 64}],
    }
    with pytest.raises(ValueError, match="verified asset"):
        GithubRelease.model_validate(upstream).asset("owner/fixture", asset)


def test_unrecognized_role_is_rejected_before_dispatch(tmp_path):
    run = subprocess.run(
        [sys.executable, "-I", "-B", "-m", "setup_system_sdk.apt", "unregistered"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 2 and not run.stdout
    assert not list(tmp_path.iterdir())


def test_caller_cannot_inject_package_options_or_a_command(tmp_path):
    for bundle in (
        {"names": ["--help"]},
        {"names": ["../pkg"]},
        {"names": ["ok"], "command": "id"},
    ):
        with pytest.raises(ValueError):
            Packages.model_validate(bundle)
    for change in ({"action_id": "shell"}, {"plugin_id": "../other"}, {"command": "id"}):
        with pytest.raises(ValueError):
            Approval.model_validate(
                {
                    "plugin_id": "example",
                    "action_id": "install",
                    "plan": {"summary": "Preview"},
                    **change,
                }
            )
    assert not list(tmp_path.iterdir())
