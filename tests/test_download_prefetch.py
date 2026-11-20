"""Verified download prefetch and offline consumption without system or GUI changes."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.error import HTTPError

import pytest

from setup_system import downloads
from setup_system.infrastructure import fonts


class Response(io.BytesIO):
    status = 200

    url = "https://release-assets.githubusercontent.com/pinned-asset"

    def geturl(self) -> str:
        return self.url


def mock_open(monkeypatch: pytest.MonkeyPatch, respond) -> None:
    class Opener:
        def open(self, *args, **kwargs):
            return respond(*args, **kwargs)

    monkeypatch.setattr(downloads, "build_opener", lambda *handlers: Opener())


@pytest.fixture
def context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, bytes]:
    home = tmp_path / "user home"
    home.mkdir(mode=0o700)
    root = tmp_path / "source"
    (root / "manifests").mkdir(parents=True)
    (root / "manifests/platforms.toml").write_bytes(
        (Path(__file__).resolve().parents[1] / "manifests/platforms.toml").read_bytes()
    )
    for name, directory in (
        ("HOME", home),
        ("XDG_CACHE_HOME", home / "cache"),
        ("XDG_DATA_HOME", home / "data"),
        ("XDG_STATE_HOME", home / "state"),
    ):
        monkeypatch.setenv(name, str(directory))
    contents = {"Regular.ttf": b"font fixture", "OFL.txt": b"license fixture"}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    data = stream.getvalue()
    font = (
        '[jetbrains_mono_nerd_font]\nversion = "3.4.0"\n'
        'url = "https://github.com/ryanoasis/nerd-fonts/releases/download/v3.4.0/JetBrainsMono.zip"\n'
        f'sha256 = "{hashlib.sha256(data).hexdigest()}"\n'
    )
    for name, value in contents.items():
        font += (
            "[[jetbrains_mono_nerd_font.files]]\n"
            f'name = "{name}"\nsha256 = "{hashlib.sha256(value).hexdigest()}"\n'
        )
    (root / "manifests/fonts.toml").write_text(font)
    return root, home, data


def test_prefetch_needs_no_graphical_commands_and_offline_reuses_bytes(
    context: tuple[Path, Path, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, home, data = context
    calls: list[object] = []
    mock_open(monkeypatch, lambda *a, **kw: calls.append(a[0]) or Response(data))
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("GUI command"))
    fonts.prefetch_fonts(root)
    archives = list((home / "cache").rglob("*.zip"))
    assert len(archives) == len(calls) == 1 and archives[0].read_bytes() == data
    provenance = json.loads(archives[0].with_suffix(".zip.json").read_text())
    assert provenance["sha256"] == hashlib.sha256(data).hexdigest()
    assert not (home / "data").exists()
    before = {p: p.read_bytes() for p in (home / "cache").rglob("*") if p.is_file()}
    mock_open(monkeypatch, lambda *a, **kw: pytest.fail("offline network"))
    fonts.prefetch_fonts(root)
    files = fonts.font_files(root, offline=True)
    assert {p.name: value for p, value in files.items()} == {
        "Regular.ttf": b"font fixture",
        "OFL.txt": b"license fixture",
    }
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize("kind", ["missing", "corrupt", "symlink"])
def test_offline_cache_failure_never_downloads_or_replaces(
    context: tuple[Path, Path, bytes], monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    root, home, data = context
    if kind != "missing":
        mock_open(monkeypatch, lambda *a, **kw: Response(data))
        fonts.prefetch_fonts(root)
        archive = next((home / "cache").rglob("*.zip"))
        archive.unlink()
        if kind == "corrupt":
            archive.write_bytes(b"user content")
        else:
            outside = home / "private file"
            outside.write_bytes(b"user content")
            archive.symlink_to(outside)
    before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    mock_open(monkeypatch, lambda *a, **kw: pytest.fail("offline network"))
    with pytest.raises((ValueError, RuntimeError)):
        fonts.font_files(root, offline=True)
    assert {p: p.read_bytes() for p in before} == before
    if kind != "missing":
        with pytest.raises((ValueError, RuntimeError)):
            fonts.prefetch_fonts(root)
        assert {p: p.read_bytes() for p in before} == before


def test_prefetch_rejects_symlinked_cache_before_network(
    context: tuple[Path, Path, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, home, _ = context
    outside = home / "other"
    outside.mkdir()
    (home / "cache").symlink_to(outside, target_is_directory=True)
    mock_open(monkeypatch, lambda *a, **kw: pytest.fail("network"))
    with pytest.raises((ValueError, RuntimeError)):
        fonts.prefetch_fonts(root)
    assert not list(outside.iterdir())


def test_download_preparation_refuses_root_before_writes(
    context: tuple[Path, Path, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, home, _ = context
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises((ValueError, RuntimeError)):
        fonts.prefetch_fonts(root)
    assert not list(home.iterdir())


def test_http_failure_reports_only_status_and_host(
    context: tuple[Path, Path, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _, _ = context
    secret = "SYNTHETIC_SECRET_QUERY"

    def failure(*args: object, **kwargs: object) -> Response:
        raise HTTPError("https://github.com/release?token=" + secret, 403, secret, {}, None)

    mock_open(monkeypatch, failure)
    with pytest.raises((ValueError, RuntimeError)) as problem:
        fonts.prefetch_fonts(root)
    assert "403" in str(problem.value) and "github.com" in str(problem.value)
    assert secret not in str(problem.value) and "token=" not in str(problem.value)


def test_installed_matching_fonts_need_no_cache_or_network(
    context: tuple[Path, Path, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, home, data = context
    destination = home / "data/fonts/JetBrainsMonoNerdFont-3.4.0"
    destination.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in ("Regular.ttf", "OFL.txt"):
            (destination / name).write_bytes(archive.read(name))
    mock_open(monkeypatch, lambda *a, **kw: pytest.fail("network"))
    assert fonts.font_files(root, offline=True) == {}
    assert not (home / "cache").exists()


def test_download_cache_rejects_foreign_file_owner(
    context: tuple[Path, Path, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, home, data = context
    mock_open(monkeypatch, lambda *a, **kw: Response(data))
    fonts.prefetch_fonts(root)
    target = next((home / "cache").rglob("*.zip"))
    original = Path.lstat

    def foreign(path: Path) -> os.stat_result:
        details = original(path)
        if path == target:
            fields = list(details)
            fields[4] = os.getuid() + 1
            return os.stat_result(fields)
        return details

    monkeypatch.setattr(Path, "lstat", foreign)
    mock_open(monkeypatch, lambda *a, **kw: pytest.fail("network"))
    with pytest.raises(ValueError, match=r"owner|owned"):
        fonts.prefetch_fonts(root)


def test_oversized_cached_font_archive_fails_without_reading_or_downloading(
    context: tuple[Path, Path, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, home, data = context
    mock_open(monkeypatch, lambda *a, **kw: Response(data))
    fonts.prefetch_fonts(root)
    target = next((home / "cache").rglob("*.zip"))
    with target.open("wb") as output:
        output.truncate(fonts.MAX_FONT_ARCHIVE_BYTES + 1)
    mock_open(monkeypatch, lambda *a, **kw: pytest.fail("network"))
    with pytest.raises(ValueError, match="bounded"):
        fonts.font_files(root, offline=True)
    assert target.stat().st_size == fonts.MAX_FONT_ARCHIVE_BYTES + 1


@pytest.mark.parametrize("kind", ["corrupt", "symlink"])
def test_offline_rejects_unknown_cache_provenance_without_rewriting(
    context: tuple[Path, Path, bytes], monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    root, home, data = context
    mock_open(monkeypatch, lambda *a, **kw: Response(data))
    fonts.prefetch_fonts(root)
    archive = next((home / "cache").rglob("*.zip"))
    receipt = archive.with_suffix(".zip.json")
    receipt.unlink()
    preserved = home / "unmanaged provenance"
    preserved.write_bytes(b"must remain unchanged")
    if kind == "corrupt":
        receipt.write_bytes(b"unmanaged provenance")
    else:
        receipt.symlink_to(preserved)
    before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    mock_open(monkeypatch, lambda *a, **kw: pytest.fail("network"))
    with pytest.raises(ValueError):
        fonts.font_files(root, offline=True)
    with pytest.raises(ValueError):
        fonts.prefetch_fonts(root)
    assert {p: p.read_bytes() for p in before} == before


def test_cached_font_fifo_is_rejected_without_blocking_or_touching_it(
    context: tuple[Path, Path, bytes],
) -> None:
    _, home, _ = context
    archive = home / "cached font.zip"
    os.mkfifo(archive, mode=0o600)
    before = archive.lstat()
    script = (
        "from pathlib import Path\n"
        "from setup_system.infrastructure.fonts import _font_archive\n"
        f"path = Path({str(archive)!r})\n"
        "try:\n"
        "    _font_archive(path, '0' * 64)\n"
        "except ValueError as exc:\n"
        "    assert 'regular file' in str(exc)\n"
        "else:\n"
        "    raise AssertionError('FIFO cache was accepted')\n"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env={**os.environ, "PYTHONPATH": str(Path(fonts.__file__).parents[2])},
        text=True,
        capture_output=True,
        timeout=3,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    after = archive.lstat()
    assert (after.st_ino, after.st_mode, after.st_size) == (
        before.st_ino,
        before.st_mode,
        before.st_size,
    )
    assert list(home.iterdir()) == [archive]


def test_pinned_font_release_fits_bounded_archive_limit() -> None:
    # Official v3.4.0 JetBrainsMono.zip asset metadata, checked independently.
    assert 129115244 <= fonts.MAX_FONT_ARCHIVE_BYTES <= 128 * 1024 * 1024


def test_download_and_offline_cache_accept_exact_shared_limit(context, monkeypatch) -> None:
    root, home, data = context
    monkeypatch.setattr(fonts, "MAX_FONT_ARCHIVE_BYTES", len(data))

    class BoundedResponse(Response):
        def read(self, size):
            assert 0 < size <= len(data) + 1
            return super().read(size)

    mock_open(monkeypatch, lambda *a, **kw: BoundedResponse(data))
    fonts.prefetch_fonts(root)
    mock_open(monkeypatch, lambda *a, **kw: pytest.fail("offline network"))
    assert fonts.font_files(root, offline=True)
    assert next((home / "cache").rglob("*.zip")).read_bytes() == data


def test_download_above_shared_limit_does_not_publish_cache(context, monkeypatch) -> None:
    root, home, data = context
    monkeypatch.setattr(fonts, "MAX_FONT_ARCHIVE_BYTES", len(data) - 1)
    mock_open(monkeypatch, lambda *a, **kw: Response(data))
    with pytest.raises(ValueError, match="Font archive exceeds"):
        fonts.prefetch_fonts(root)
    assert not (home / "cache").exists()


def test_cache_above_shared_limit_is_preserved_without_network(context, monkeypatch) -> None:
    root, _home, data = context
    archive, receipt, _ = fonts._font_cache(fonts._font_manifest(root))
    archive.parent.mkdir(parents=True)
    archive.write_bytes(data)
    monkeypatch.setattr(fonts, "MAX_FONT_ARCHIVE_BYTES", len(data) - 1)
    mock_open(monkeypatch, lambda *a, **kw: pytest.fail("network"))
    with pytest.raises(ValueError, match="bounded"):
        fonts.prefetch_fonts(root)
    assert archive.read_bytes() == data and not receipt.exists()
