"""Use a tiny disposable Git repository to exercise immutable Flutter checkout readback."""

import subprocess

import pytest
import setup_plugin_dart_flutter as flutter
from setup_plugin_dart_flutter import Plugin, Settings


def test_approved_commit_not_current_branch(tmp_path, monkeypatch):
    upstream = tmp_path / "upstream"
    subprocess.run(["git", "init", "--quiet", "--template=", str(upstream)], check=True)

    def commit(value):
        (upstream / "README").write_text(value)
        subprocess.run(["git", "-C", str(upstream), "add", "README"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(upstream),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgSign=false",
                "-c",
                "core.hooksPath=/dev/null",
                "commit",
                "--quiet",
                "-m",
                value,
            ],
            check=True,
        )
        return subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"]).strip()

    revision = commit("approved")
    commit("later")
    monkeypatch.setattr(flutter, "REPOSITORY", str(upstream))
    plugin = Plugin()
    target = tmp_path / "target"
    target.mkdir()
    plugin.install_tools(tmp_path, target, revision)
    assert (target / "checkout/README").read_text() == "approved"
    with pytest.raises(ValueError):
        plugin.install_tools(tmp_path, target, b"--help")
    assert set(plugin.commands(Settings(publish_dart=False))) == {"flutter"}
