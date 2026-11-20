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
from setup_system.application import happ, workstation
from setup_system.planning import Platform


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
    package = b"pinned Happ fixture"
    (root / "manifests/happ.toml").write_text(
        'version = "4.5.4"\npackageVersion = "4.5.4-333"\n[linux.amd64]\n'
        'url = "https://github.com/Happ-proxy/happ-desktop/releases/download/4.5.4/Happ.linux.x64.deb"\n'
        f'sha256 = "{hashlib.sha256(package).hexdigest()}"\npackageArchitecture = "amd64"\n'
    )
    monkeypatch.setattr(
        happ, "detect_platform", lambda: Platform("debian", "13", "x86_64", True, False, "")
    )
    monkeypatch.setattr(
        happ,
        "_run",
        lambda argv: subprocess.CompletedProcess(
            argv, 0, "Package: happ\nVersion: 4.5.4-333\nArchitecture: amd64\n", ""
        ),
    )
    return root, home, data


def test_prefetch_needs_no_graphical_commands_and_offline_reuses_bytes(
    context: tuple[Path, Path, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, home, data = context
    calls: list[object] = []
    mock_open(monkeypatch, lambda *a, **kw: calls.append(a[0]) or Response(data))
    monkeypatch.setattr(workstation, "_run", lambda *a: pytest.fail("GUI command"))
    monkeypatch.setattr(workstation, "_required_commands", lambda *a: pytest.fail("GUI stage"))
    workstation.prefetch_workstation(root)
    archives = list((home / "cache").rglob("*.zip"))
    assert len(archives) == len(calls) == 1 and archives[0].read_bytes() == data
    provenance = json.loads(archives[0].with_suffix(".zip.json").read_text())
    assert provenance["sha256"] == hashlib.sha256(data).hexdigest()
    assert not (home / "data").exists()
    before = {p: p.read_bytes() for p in (home / "cache").rglob("*") if p.is_file()}
    mock_open(monkeypatch, lambda *a, **kw: pytest.fail("offline network"))
    workstation.prefetch_workstation(root)
    files = workstation._font_files(root, offline=True)
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
        workstation.prefetch_workstation(root)
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
        workstation._font_files(root, offline=True)
    assert {p: p.read_bytes() for p in before} == before
    if kind != "missing":
        with pytest.raises((ValueError, RuntimeError)):
            workstation.prefetch_workstation(root)
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
        workstation.prefetch_workstation(root)
    assert not list(outside.iterdir())


@pytest.mark.parametrize("operation", ["font", "happ"])
def test_download_preparation_refuses_root_before_writes(
    context: tuple[Path, Path, bytes], monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    root, home, _ = context
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises((ValueError, RuntimeError)):
        if operation == "font":
            workstation.prefetch_workstation(root)
        else:
            happ.download_happ(root, home / "happ.deb")
    assert not list(home.iterdir())


def test_happ_download_is_verified_nonroot_and_does_not_touch_private_state(
    context: tuple[Path, Path, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, home, _ = context
    private = home / "Happ.conf"
    private.write_bytes(b"private settings stay intact")
    target = home / "session" / "happ.deb"
    package = b"pinned Happ fixture"
    mock_open(monkeypatch, lambda *a, **kw: Response(package))
    assert happ.download_happ(root, target) == target
    assert target.read_bytes() == package and target.stat().st_uid == os.getuid()
    assert target.stat().st_mode & 0o777 == 0o600
    mock_open(monkeypatch, lambda *a, **kw: pytest.fail("repeat network"))
    assert happ.download_happ(root, target) == target
    assert private.read_bytes() == b"private settings stay intact"


@pytest.mark.parametrize("kind", ["existing", "symlink"])
def test_happ_never_overwrites_unknown_destinations(
    context: tuple[Path, Path, bytes], monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    root, home, _ = context
    target = home / "happ.deb"
    private = home / "private"
    private.write_bytes(b"keep")
    if kind == "existing":
        target.write_bytes(b"user file")
    else:
        target.symlink_to(private)
    mock_open(monkeypatch, lambda *a, **kw: pytest.fail("network"))
    with pytest.raises((ValueError, RuntimeError)):
        happ.download_happ(root, target)
    assert private.read_bytes() == b"keep"
    assert target.is_symlink() if kind == "symlink" else target.read_bytes() == b"user file"


@pytest.mark.parametrize("operation", ["font", "happ"])
def test_http_failure_reports_only_status_and_host(
    context: tuple[Path, Path, bytes], monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    root, home, _ = context
    secret = "SYNTHETIC_SECRET_QUERY"

    def failure(*args: object, **kwargs: object) -> Response:
        raise HTTPError("https://github.com/release?token=" + secret, 403, secret, {}, None)

    mock_open(monkeypatch, failure)
    with pytest.raises((ValueError, RuntimeError)) as problem:
        if operation == "font":
            workstation.prefetch_workstation(root)
        else:
            happ.download_happ(root, home / "happ.deb")
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
    assert workstation._font_files(root, offline=True) == {}
    assert not (home / "cache").exists()


@pytest.mark.parametrize("operation", ["font", "happ"])
def test_download_cache_rejects_foreign_file_owner(
    context: tuple[Path, Path, bytes], monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    root, home, data = context
    if operation == "font":
        mock_open(monkeypatch, lambda *a, **kw: Response(data))
        workstation.prefetch_workstation(root)
        target = next((home / "cache").rglob("*.zip"))
    else:
        target = home / "happ.deb"
        target.write_bytes(b"pinned Happ fixture")
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
        if operation == "font":
            workstation.prefetch_workstation(root)
        else:
            happ.download_happ(root, target)


@pytest.mark.parametrize("kind", ["digest", "metadata"])
def test_happ_rejects_invalid_package_without_publishing_destination(
    context: tuple[Path, Path, bytes], monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    root, home, _ = context
    package = b"corrupt" if kind == "digest" else b"pinned Happ fixture"
    mock_open(monkeypatch, lambda *a, **kw: Response(package))
    if kind == "metadata":
        monkeypatch.setattr(
            happ, "_run", lambda argv: subprocess.CompletedProcess(argv, 0, "Package: other\n", "")
        )
    target = home / "happ.deb"
    with pytest.raises(ValueError):
        happ.download_happ(root, target)
    assert not target.exists() and not list(home.glob(".happ-*"))


def test_oversized_cached_font_archive_fails_without_reading_or_downloading(
    context: tuple[Path, Path, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, home, data = context
    mock_open(monkeypatch, lambda *a, **kw: Response(data))
    workstation.prefetch_workstation(root)
    target = next((home / "cache").rglob("*.zip"))
    with target.open("wb") as output:
        output.truncate(workstation.MAX_FONT_ARCHIVE_BYTES + 1)
    mock_open(monkeypatch, lambda *a, **kw: pytest.fail("network"))
    with pytest.raises(ValueError, match="bounded"):
        workstation._font_files(root, offline=True)
    assert target.stat().st_size == workstation.MAX_FONT_ARCHIVE_BYTES + 1


def test_happ_setup_reuses_download_helper_and_preserves_application_api(
    context: tuple[Path, Path, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, home, _ = context
    observed: list[Path] = []
    count = 0

    def inspect(platform: Platform) -> happ.HappStatus:
        nonlocal count
        count += 1
        return happ.HappStatus(
            "absent" if count == 1 else "deb 4.5.4-333 amd64",
            False,
            False,
            "unknown",
            "unknown",
            "unknown",
            "unknown",
            None,
        )

    def download(project: Path, destination: Path) -> Path:
        assert project == root
        observed.append(destination)
        return destination

    def run(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        assert argv[:4] == ["/fixture/sudo", "-n", "apt-get", "install"]
        assert argv[-1] == str(observed[-1])
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(happ, "inspect_happ", inspect)
    monkeypatch.setattr(happ, "download_happ", download)
    monkeypatch.setattr(happ.shutil, "which", lambda name: "/fixture/sudo")
    monkeypatch.setattr(happ, "_run", lambda argv: subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(happ.subprocess, "run", run)
    result = happ.setup_happ(root, Platform("debian", "13", "x86_64", True, False, ""))
    assert result.startswith("changed:") and observed[0].is_relative_to(home)


@pytest.mark.parametrize("kind", ["corrupt", "symlink"])
def test_offline_rejects_unknown_cache_provenance_without_rewriting(
    context: tuple[Path, Path, bytes], monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    root, home, data = context
    mock_open(monkeypatch, lambda *a, **kw: Response(data))
    workstation.prefetch_workstation(root)
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
        workstation._font_files(root, offline=True)
    with pytest.raises(ValueError):
        workstation.prefetch_workstation(root)
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
        "from setup_system.application.workstation import _font_archive\n"
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
        env={**os.environ, "PYTHONPATH": str(Path(workstation.__file__).parents[2])},
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
    assert 129115244 <= workstation.MAX_FONT_ARCHIVE_BYTES <= 128 * 1024 * 1024


def test_download_and_offline_cache_accept_exact_shared_limit(context, monkeypatch) -> None:
    root, home, data = context
    monkeypatch.setattr(workstation, "MAX_FONT_ARCHIVE_BYTES", len(data))

    class BoundedResponse(Response):
        def read(self, size):
            assert 0 < size <= len(data) + 1
            return super().read(size)

    mock_open(monkeypatch, lambda *a, **kw: BoundedResponse(data))
    workstation.prefetch_workstation(root)
    mock_open(monkeypatch, lambda *a, **kw: pytest.fail("offline network"))
    assert workstation._font_files(root, offline=True)
    assert next((home / "cache").rglob("*.zip")).read_bytes() == data


def test_download_above_shared_limit_does_not_publish_cache(context, monkeypatch) -> None:
    root, home, data = context
    monkeypatch.setattr(workstation, "MAX_FONT_ARCHIVE_BYTES", len(data) - 1)
    mock_open(monkeypatch, lambda *a, **kw: Response(data))
    with pytest.raises(ValueError, match="Font archive exceeds"):
        workstation.prefetch_workstation(root)
    assert not (home / "cache").exists()


def test_cache_above_shared_limit_is_preserved_without_network(context, monkeypatch) -> None:
    root, _home, data = context
    archive, receipt, _ = workstation._font_cache(workstation._font_manifest(root))
    archive.parent.mkdir(parents=True)
    archive.write_bytes(data)
    monkeypatch.setattr(workstation, "MAX_FONT_ARCHIVE_BYTES", len(data) - 1)
    mock_open(monkeypatch, lambda *a, **kw: pytest.fail("network"))
    with pytest.raises(ValueError, match="bounded"):
        workstation.prefetch_workstation(root)
    assert archive.read_bytes() == data and not receipt.exists()
