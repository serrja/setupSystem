"""Hyprland profile contracts without installing packages or starting a session."""

from __future__ import annotations

import hashlib
import json
import os
import runpy
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
from native_workspace import ROOT, apply, drift, files, render

from setup_system.diagnostics import PartialApplyError
from setup_system.file_tasks import restore
from setup_system.infrastructure.native_target import architecture as debian_architecture
from setup_system.infrastructure.native_target import target


def packages():
    native = runpy.run_path(str(ROOT / "ansible/filter_plugins/native.py"))
    return native["package_names"](
        json.dumps({"hyprland": True, "graphical_login": True}),
        json.dumps(tomllib.loads((ROOT / "config/base/packages.toml").read_text())),
        json.dumps({}),
    )


def desktop_module():
    return runpy.run_path(str(ROOT / "ansible/files/facts.py"))


@pytest.mark.parametrize("architecture", ["x86_64", "amd64", "aarch64", "arm64"])
def test_packages_are_one_additive_official_debian_profile(architecture: str) -> None:
    assert target(
        {"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"},
        architecture,
        debian_architecture(architecture),
    )[1]
    names = packages()
    assert "hyprland/trixie-backports" in names and "hyprland-guiutils/trixie-backports" in names
    assert {"greetd", "gtkgreet", "cage", "thunar"} <= set(names)
    assert not any(
        word in " ".join(names) for word in ("purge", "autoremove", "task-gnome", "kde-full")
    )


@pytest.mark.parametrize(
    ("release", "architecture"),
    [
        ({"ID": "debian", "VERSION_ID": "12", "VERSION_CODENAME": "bookworm"}, "amd64"),
        ({"ID": "ubuntu", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"}, "amd64"),
        ({"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "fork"}, "amd64"),
        ({"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"}, "riscv64"),
    ],
)
def test_unsupported_hyprland_target(release: dict[str, str], architecture: str) -> None:
    assert not target(release, architecture, debian_architecture(architecture))[1]


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "home with ' quotes"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    for key, value in (
        ("XDG_CONFIG_HOME", path / "custom config"),
        ("XDG_DATA_HOME", path / "custom data"),
        ("XDG_STATE_HOME", path / "custom state"),
        ("XDG_CACHE_HOME", path / "custom cache"),
    ):
        monkeypatch.setenv(key, str(value))
    return path


def snapshot(home: Path) -> dict[str, str]:
    return {
        str(p.relative_to(home)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in home.rglob("*")
        if p.is_file()
    }


def test_read_only_plan_does_not_require_installed_compositor(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = drift(ROOT)
    assert any(p.name == "start-hyprland" for p in result)
    assert snapshot(home) == {}


def test_apply_uses_chosen_xdg_and_repeat_has_no_new_backup(home: Path) -> None:
    backup = apply(ROOT)
    before = snapshot(home)
    assert (Path(os.environ["XDG_CONFIG_HOME"]) / "hypr/hyprland.lua").is_file()
    start = Path(os.environ["XDG_DATA_HOME"]) / "setup-system/bin/start-hyprland"
    assert start.is_file() and start.stat().st_mode & 0o111
    assert "/usr/bin/start-hyprland" in start.read_text()
    subprocess.run(["sh", "-n", str(start)], check=True, timeout=5)
    assert apply(ROOT) == backup
    assert snapshot(home) == before
    for path in home.rglob("*"):
        assert path.stat().st_uid == os.getuid()


def test_edited_file_is_preserved_and_restore_refuses_later_edits(home: Path) -> None:
    backup = apply(ROOT)
    config = Path(os.environ["XDG_CONFIG_HOME"]) / "hypr/setup-system/profile.lua"
    config.write_text("-- owner edit\n")
    before = snapshot(home)
    with pytest.raises(ValueError, match="Locally edited"):
        apply(ROOT)
    with pytest.raises(ValueError, match="Rollback conflicts"):
        restore(backup.name, apply=True)
    assert snapshot(home) == before


def test_unknown_entrypoint_is_not_overwritten(home: Path) -> None:
    entry = Path(os.environ["XDG_CONFIG_HOME"]) / "hypr/hyprland.lua"
    entry.parent.mkdir(parents=True)
    entry.write_text("-- existing owner configuration\n")
    before = snapshot(home)
    with pytest.raises(ValueError, match="unmanaged"):
        apply(ROOT)
    assert snapshot(home) == before


def test_symlink_target_rejected_before_backup(home: Path) -> None:
    config = Path(os.environ["XDG_CONFIG_HOME"])
    config.mkdir()
    (config / "hypr").symlink_to(home / "elsewhere", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        apply(ROOT)
    assert not Path(os.environ["XDG_STATE_HOME"]).exists()


def test_backup_restore_removes_created_files_and_preserves_owner_override(home: Path) -> None:
    backup = apply(ROOT)
    extra = Path(os.environ["XDG_CONFIG_HOME"]) / "hypr/local.lua"
    extra.write_text("-- owner override\n")
    preview = restore(backup.name)
    assert preview and extra.read_text() == "-- owner override\n"
    restore(backup.name, apply=True)
    assert extra.read_text() == "-- owner override\n"
    assert not (Path(os.environ["XDG_CONFIG_HOME"]) / "hypr/hyprland.lua").exists()


def test_profile_has_installed_roles_and_no_hardcoded_home() -> None:
    profile = (ROOT / "assets/hyprland/profile.lua").read_text()
    session = (ROOT / "assets/hyprland/session.sh").read_text()
    for command in ("foot", "wofi", "thunar", "pavucontrol", "playerctl"):
        assert command in profile
    assert "panel.py" in session
    assert "waybar" in (ROOT / "assets/hyprland/panel.py").read_text()
    for command in ("mako", "hypridle", "swaybg", "hyprpolkitagent"):
        assert command in session
    assert "/home/" not in profile
    json.loads(render((ROOT / "assets/hyprland/waybar.json").read_text()))


@pytest.mark.parametrize(
    "version", ["", "install ok installed 0.54.0", "deinstall ok config-files 0.56.0"]
)
def test_missing_or_incompatible_compositor_blocks_before_writes(home, monkeypatch, version):
    probe = desktop_module()
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, version, "")
    )
    assert "Lua configuration" in probe["desktop_error"]()
    assert snapshot(home) == {}


def test_interrupted_user_apply_can_resume(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from setup_system.infrastructure import managed_files

    original = managed_files._publish_changes

    def interrupted(rows, record_write):
        original(rows[:1], record_write)
        raise OSError("simulated user-stage storage failure")

    monkeypatch.setattr(managed_files, "_publish_changes", interrupted)
    with pytest.raises(PartialApplyError, match="simulated"):
        apply(ROOT)
    assert snapshot(home)
    monkeypatch.setattr(managed_files, "_publish_changes", original)
    backup = apply(ROOT)
    before = snapshot(home)
    assert apply(ROOT) == backup
    assert snapshot(home) == before


def test_incomplete_system_stage_blocks_user_writes(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = desktop_module()
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, "install ok installed 0.55.2", ""),
    )
    monkeypatch.setattr(os, "access", lambda path, mode: Path(path).name != "thunar")
    assert "missing: thunar" in probe["desktop_error"]()
    assert snapshot(home) == {}


def test_login_wrapper_permissions_recover_without_overwriting_contents(home: Path) -> None:
    apply(ROOT)
    start = Path(os.environ["XDG_DATA_HOME"]) / "setup-system/bin/start-hyprland"
    content = start.read_bytes()
    start.chmod(0o600)
    apply(ROOT)
    assert start.read_bytes() == content
    assert start.stat().st_mode & 0o100


def test_base_selection_keeps_security_and_updates_candidates_eligible() -> None:
    names = packages()
    assert "pipewire-audio" in names and "pipewire-audio/trixie" not in names
    assert "hypridle/trixie-backports" in names and all("=" not in name for name in names)


@pytest.fixture
def brightness_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    tools = tmp_path / "mock tools"
    tools.mkdir()
    calls = tmp_path / "busctl calls.jsonl"
    mock = tools / "busctl"
    mock.write_text(
        "#!/usr/bin/env python3\n"
        "import json,os,sys\n"
        "from pathlib import Path\n"
        "with Path(os.environ['BRIGHTNESS_CALLS']).open('a') as stream:\n"
        " stream.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "if 'get-property' in sys.argv:\n"
        " key = 'BRIGHTNESS_ACTIVE' if sys.argv[-1]=='Active' else 'BRIGHTNESS_REMOTE'\n"
        " print('b '+os.environ.get(key,'true' if sys.argv[-1]=='Active' else 'false'))\n"
        "elif os.environ.get('BRIGHTNESS_FAIL'):\n"
        " print('Synthetic permission denied',file=sys.stderr); sys.exit(1)\n"
    )
    mock.chmod(0o700)
    root = tmp_path / "mock sysfs"
    root.mkdir()
    environment = {
        "PATH": str(tools) + os.pathsep + os.environ["PATH"],
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "BRIGHTNESS_CALLS": str(calls),
    }
    return root, calls, environment


def run_brightness(
    fixture: tuple[Path, Path, dict[str, str]], direction: str
) -> subprocess.CompletedProcess[str]:
    root, _, environment = fixture
    return subprocess.run(
        [
            "sh",
            "-c",
            '. "$1"; brightness_main "$2" "$3"',
            "fixture",
            str(ROOT / "assets/hyprland/brightness.sh"),
            direction,
            str(root),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def backlight(root: Path, name: str, current: int, maximum: int, kind: str = "raw") -> None:
    device = root / name
    device.mkdir()
    (device / "brightness").write_text(str(current) + "\n")
    (device / "max_brightness").write_text(str(maximum) + "\n")
    (device / "type").write_text(kind + "\n")


@pytest.mark.parametrize(
    ("direction", "current", "expected"),
    [("up", 50, "55"), ("up", 99, "100"), ("down", 50, "45"), ("down", 3, "2")],
)
def test_brightness_uses_logind_and_clamps_without_sysfs_writes(
    brightness_fixture: tuple[Path, Path, dict[str, str]],
    direction: str,
    current: int,
    expected: str,
) -> None:
    root, calls, _ = brightness_fixture
    backlight(root, "actual_panel", current, 100)
    result = run_brightness(brightness_fixture, direction)
    assert result.returncode == 0, result.stderr
    arguments = [json.loads(line) for line in calls.read_text().splitlines()]
    assert arguments[-1] == [
        "--system",
        "--timeout=5",
        "call",
        "org.freedesktop.login1",
        "/org/freedesktop/login1/session/auto",
        "org.freedesktop.login1.Session",
        "SetBrightness",
        "ssu",
        "backlight",
        "actual_panel",
        expected,
    ]
    assert (root / "actual_panel/brightness").read_text() == str(current) + "\n"


def test_brightness_prefers_real_raw_device(
    brightness_fixture: tuple[Path, Path, dict[str, str]],
) -> None:
    root, calls, _ = brightness_fixture
    backlight(root, "a_firmware", 10, 100, "firmware")
    backlight(root, "z_raw", 10, 100)
    assert run_brightness(brightness_fixture, "up").returncode == 0
    assert json.loads(calls.read_text().splitlines()[-1])[-2] == "z_raw"


@pytest.mark.parametrize("condition", ["missing", "inactive", "remote", "denied"])
def test_brightness_missing_hardware_or_permission_fails_explicitly(
    brightness_fixture: tuple[Path, Path, dict[str, str]], condition: str
) -> None:
    root, calls, environment = brightness_fixture
    if condition != "missing":
        backlight(root, "panel", 50, 100)
    if condition == "inactive":
        environment["BRIGHTNESS_ACTIVE"] = "false"
    elif condition == "remote":
        environment["BRIGHTNESS_REMOTE"] = "true"
    elif condition == "denied":
        environment["BRIGHTNESS_FAIL"] = "1"
    result = run_brightness(brightness_fixture, "up")
    assert result.returncode != 0 and result.stderr
    expected = {
        "missing": "No supported backlight device",
        "inactive": "local active logind session",
        "remote": "local active logind session",
        "denied": "Synthetic permission denied",
    }
    assert expected[condition] in result.stderr
    if condition in {"missing", "inactive", "remote"} and calls.exists():
        assert all("SetBrightness" not in line for line in calls.read_text().splitlines())


def test_brightness_plan_requires_busctl_without_privileged_groups() -> None:
    import tomllib

    profile = tomllib.loads((ROOT / "config/base/desktop.toml").read_text())
    assert "busctl" in profile["commands"]
    assert "brightnessctl" not in packages()
    lua = (ROOT / "assets/hyprland/profile.lua").read_text()
    assert 'quote(cfg .. "/brightness.sh")' in lua
    helper = (ROOT / "assets/hyprland/brightness.sh").read_text()
    assert "sudo" not in helper and "chmod" not in helper and "chgrp" not in helper


@pytest.mark.parametrize("direction", ["", "toggle", "up; false"])
def test_brightness_rejects_unknown_operation_without_system_bus(
    brightness_fixture: tuple[Path, Path, dict[str, str]], direction: str
) -> None:
    _, calls, _ = brightness_fixture
    result = run_brightness(brightness_fixture, direction)
    assert result.returncode == 2 and "Choose brightness" in result.stderr
    assert not calls.exists()


@pytest.mark.parametrize("version", ["0.55.2+ds-1~bpo13+1", "0.55.20", "0.56.0"])
def test_version_inspection_does_not_launch_compositor_without_runtime(
    monkeypatch: pytest.MonkeyPatch,
    version: str,
) -> None:
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(os, "access", lambda path, mode: True)

    def execute(
        argv: list[str], *, text: bool, capture_output: bool, check: bool, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        assert argv[0] == "/usr/bin/dpkg-query"
        assert argv[-1] == "hyprland"
        return subprocess.CompletedProcess(argv, 0, f"install ok installed\t{version}\n", "")

    monkeypatch.setattr(subprocess, "run", execute)
    assert desktop_module()["desktop_error"]() is None


@pytest.fixture
def profile_source(tmp_path: Path) -> Path:
    source = tmp_path / "source checkout"
    for relative in (
        "assets/hyprland",
        "assets/fonts",
        "config/base",
        "ansible/templates",
    ):
        shutil.copytree(
            ROOT / relative,
            source / relative,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
    return source


def test_source_assets_ignore_bytecode_and_keep_panel_provenance(
    home: Path,
    profile_source: Path,
) -> None:
    assets = profile_source / "assets/hyprland"
    generated = assets / "__pycache__/panel.fixture.pyc"
    generated.parent.mkdir(exist_ok=True)
    generated.write_bytes(b"\xff\x00generated bytecode")
    (assets / "panel.pyc").write_bytes(b"\xff\x00legacy bytecode")
    (assets / "scratch.txt").write_text("This local file is not a profile asset.\n")
    before = snapshot(home)
    result = drift(profile_source)
    assert snapshot(home) == before
    assert not any(
        "__pycache__" in path.parts or path.name.endswith((".pyc", "scratch.txt"))
        for path in result
    )
    backup = apply(profile_source)
    managed = Path(os.environ["XDG_CONFIG_HOME"]) / "hypr/setup-system"
    panel = managed / "panel.py"
    assert panel.read_bytes() == (assets / "panel.py").read_bytes()
    assert not (managed / "__pycache__").exists()
    assert not (managed / "panel.pyc").exists()
    assert not (managed / "scratch.txt").exists()
    operation = json.loads((backup / "operation.json").read_text())
    entry = next(
        row for row in operation["files"] if row["relative"] == str(panel.relative_to(home))
    )
    assert entry["after"]["sha256"] == hashlib.sha256(panel.read_bytes()).hexdigest()
    assert generated.read_bytes() == b"\xff\x00generated bytecode"


@pytest.mark.parametrize("damage", ["missing", "directory", "symlink", "malformed"])
def test_source_assets_reject_incomplete_or_malformed_intended_panel(
    home: Path,
    profile_source: Path,
    damage: str,
) -> None:
    panel = profile_source / "assets/hyprland/panel.py"
    panel.unlink()
    if damage == "directory":
        panel.mkdir()
    elif damage == "symlink":
        panel.symlink_to(ROOT / "assets/hyprland/panel.py")
    elif damage == "malformed":
        panel.write_bytes(b"\xffnot UTF-8 source")
    with pytest.raises(ValueError):
        drift(profile_source)
    with pytest.raises(ValueError):
        apply(profile_source)
    assert snapshot(home) == {}


def test_workspace_theme_does_not_write_session_entrypoints(home: Path) -> None:
    targets, _ = files(ROOT, {"desktop_theme": True})
    assert any(path.name == "profile.lua" for path in targets)
    assert not any(
        path.name in {"start-hyprland", "hyprland.lua", "session.sh"} for path in targets
    )
    assert snapshot(home) == {}
