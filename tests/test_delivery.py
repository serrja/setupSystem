"""Delivery prepares a locked user runtime without a second system installer."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from setup_system import _delivery_entry as delivery
from setup_system.installation import TargetUser


@pytest.fixture
def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    source = tmp_path / "released source"
    for directory in ("src", "assets", "manifests"):
        (source / directory).mkdir(parents=True)
    for filename in ("pyproject.toml", "uv.lock", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        (source / filename).write_text("Fixture verified source\n")
    (source / "src/module.py").write_text("# Fixture source\n")
    home = tmp_path / "home with spaces"
    home.mkdir()
    target = TargetUser("fixture", os.getuid(), os.getgid(), home, "/bin/sh")
    monkeypatch.setattr(delivery, "resolve_target", lambda name: target)
    monkeypatch.setattr(delivery.UserRuntime, "prepare", lambda self: home / "runtime")
    uv = tmp_path / "verified uv"
    uv.write_bytes(b"fixture executable")
    return source, uv, home


def test_delivery_and_repeat_preserve_source_and_existing_release(
    prepared: tuple[Path, Path, Path],
) -> None:
    source, uv, home = prepared
    previous_environment = dict(os.environ)
    destination, command = delivery.prepare(source, "v1.2.3", uv)
    assert dict(os.environ) == previous_environment
    assert destination == home / ".local/share/setup-system/releases/v1.2.3"
    assert (destination / "src/module.py").read_bytes() == (source / "src/module.py").read_bytes()
    assert command == home / "runtime/bin/setup-system"
    binary = home / ".local/share/setup-system/bin/uv"
    assert binary.read_bytes() == uv.read_bytes() and binary.stat().st_mode & 0o777 == 0o755
    before = (destination / "src/module.py").stat().st_mtime_ns
    assert delivery.prepare(source, "v1.2.3", uv) == (destination, command)
    assert (destination / "src/module.py").stat().st_mtime_ns == before
    assert not (source / ".venv").exists()


def test_delivery_never_overwrites_modified_source(prepared: tuple[Path, Path, Path]) -> None:
    source, uv, _ = prepared
    destination, _ = delivery.prepare(source, "v1.2.3", uv)
    edited = destination / "src/module.py"
    edited.write_text("# Owner change\n")
    with pytest.raises(ValueError, match="differs"):
        delivery.prepare(source, "v1.2.3", uv)
    assert edited.read_text() == "# Owner change\n"


def test_delivery_rejects_root_before_write(
    prepared: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    source, uv, home = prepared
    monkeypatch.setattr(delivery.os, "geteuid", lambda: 0)
    with pytest.raises(ValueError, match="desktop user"):
        delivery.prepare(source, "v1.2.3", uv)
    assert list(home.iterdir()) == []


def test_delivery_rejects_symlink_destination_before_write(
    prepared: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    source, uv, home = prepared
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / ".local").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        delivery.prepare(source, "v1.2.3", uv)
    assert list(outside.iterdir()) == []
