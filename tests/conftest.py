"""Shared synthetic user environments."""

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "Юникод home"
    path.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(path))
    for name in ("CONFIG", "STATE", "CACHE", "DATA"):
        monkeypatch.setenv(f"XDG_{name}_HOME", str(path / name.lower()))
    return path
