"""Confirm the real packaged Neovim files, compatible tag selection and local changes."""

import subprocess

import pytest
from pydantic import ValidationError
from setup_plugin_neovim import Inputs, Plugin, Search, Settings, extensions

from setup_system_sdk import Outcome, Plan


def test_files_have_exact_palette_and_search_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for scope in ("CONFIG", "DATA", "STATE", "CACHE"):
        monkeypatch.setenv(f"XDG_{scope}_HOME", str(tmp_path / scope.lower()))
    files = Plugin().files("configure", Settings(), Inputs())
    assert all(b"@{" not in b for b in files.values())
    config = files[tmp_path / "config/nvim/init.lua"]
    assert b'vim.fn.shellescape("!" .. glob)' in config and b"modules.txt" in config
    assert b'base = "#1e1e2e"' in files[tmp_path / "config/nvim/colors/catppuccin_mocha_green.lua"]
    assert b"missing = false" in config


@pytest.mark.parametrize("globs", [[], [""], ["!passwords"], ["x\ny"], [False]])
def test_closed_search_catalog(globs):
    with pytest.raises(ValidationError):
        Search(exclude_globs=globs)


def test_resolve_stable_annotated_tag_and_guard_dirty_checkout(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    source = extensions.Source(repo="https://github.com/example/fixture.git", major=1)
    checkout = tmp_path / "editor"
    subprocess.run(["git", "init", "--quiet", str(checkout)], check=True)
    subprocess.run(["git", "-C", str(checkout), "remote", "add", "origin", source.repo], check=True)
    (checkout / "personal").write_text("keep")
    with pytest.raises(ValueError, match="local changes"):
        extensions.observe(checkout, source)
    assert (checkout / "personal").read_text() == "keep"
    refs = {"v1.1.0": "1", "v1.10.0": "2", "v1.10.0^{}": "3", "v1.11.0-beta": "4", "v2.0.0": "5"}
    monkeypatch.setattr(
        extensions,
        "git",
        lambda *args: "\n".join(char * 40 + "\trefs/tags/" + tag for tag, char in refs.items()),
    )
    assert extensions.revision(source) == "3" * 40


def test_unverified_editor_or_failed_smoke_cannot_claim_success(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    with pytest.raises(ValueError, match="Verify the managed"):
        extensions.nvim("version", [])
    monkeypatch.setattr(extensions, "nvim", lambda *a: False)
    assert extensions.verify_parsers(Plan(summary="test"), ["python"]).outcome == Outcome.PARTIAL
