"""Hyprland profile contracts without installing packages or starting a session."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


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
            str(
                Path(__file__).resolve().parents[1] / "src/setup_plugin_waybar/assets/brightness.sh"
            ),
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


@pytest.mark.parametrize("direction", ["", "toggle", "up; false"])
def test_brightness_rejects_unknown_operation_without_system_bus(
    brightness_fixture: tuple[Path, Path, dict[str, str]], direction: str
) -> None:
    _, calls, _ = brightness_fixture
    result = run_brightness(brightness_fixture, direction)
    assert result.returncode == 2 and "Choose brightness" in result.stderr
    assert not calls.exists()
