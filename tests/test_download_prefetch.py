"""The offline template cache and distribution-managed fonts have separate lifecycles."""

import hashlib
import os
import runpy
import tomllib
from pathlib import Path

import pytest

from setup_system.application.templates import load_templates
from setup_system.infrastructure import copier_templates

ROOT = Path(__file__).resolve().parents[1]


def test_font_packages_are_selected_with_the_desktop_without_an_archive_download():
    catalog = tomllib.loads((ROOT / "config/base/packages.toml").read_text())
    packages = runpy.run_path(str(ROOT / "ansible/filter_plugins/native.py"))["package_names"]
    selection = packages({"hyprland": True}, catalog, {})
    assert {"fontconfig", "fonts-jetbrains-mono", "fonts-noto-color-emoji"} <= set(selection)


@pytest.mark.parametrize(
    "problem",
    [
        "fifo",
        "too_large",
        "corrupt",
        "symlink",
        "foreign_owner",
        "group_writable",
        "external_hardlink",
    ],
)
def test_invalid_cache_metadata_is_preserved_without_network(tmp_path, monkeypatch, problem):
    source = load_templates(ROOT)["python"]
    root = tmp_path / "cache"
    raw = b"unmanaged metadata"
    entry = root / source.metadata.alias / hashlib.sha256(b"expected").hexdigest()
    entry.mkdir(parents=True)
    target = entry / "catalog.json"
    outside = tmp_path / "local file"
    outside.write_bytes(raw)
    if problem == "fifo":
        os.mkfifo(target, mode=0o600)
    elif problem == "symlink":
        target.symlink_to(outside)
    elif problem == "external_hardlink":
        os.link(outside, target)
    else:
        target.write_bytes(raw)
        target.chmod(0o600)
        if problem == "too_large":
            with target.open("ab") as stream:
                stream.truncate(copier_templates.MAX_CACHE_IDENTITY + 1)
        elif problem == "foreign_owner":
            monkeypatch.setattr(os, "getuid", lambda: target.stat().st_uid + 1)
        elif problem == "group_writable":
            target.chmod(0o666)
    before = target.lstat()
    monkeypatch.setattr(
        copier_templates, "_fetch_ref", lambda *args: pytest.fail("offline network")
    )
    with pytest.raises(ValueError):
        copier_templates.available_versions(source, root, offline=True)
    after = target.lstat()
    assert (before.st_ino, before.st_size, before.st_mode) == (
        after.st_ino,
        after.st_size,
        after.st_mode,
    )
    assert outside.read_bytes() == raw and not list(root.rglob("source.git"))
