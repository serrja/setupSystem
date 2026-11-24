"""Only the Textual entrypoint remains public; help and rejected inputs are read-only."""

import subprocess
import sys
from pathlib import Path

import pytest

from setup_system import entrypoint


def test_only_textual_entrypoint_is_published() -> None:
    import tomllib

    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    assert project["scripts"] == {"setup-system": "setup_system.entrypoint:main"}
    assert not (root / "setup.sh").exists()
    assert not (root / "src/setup_system/cli/main.py").exists()


def test_help_is_read_only_without_importing_textual(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            "import runpy,sys; sys.argv=['setup-system','--help']; "
            "runpy.run_module('setup_system',run_name='__main__'); "
            "assert 'textual' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Textual" in result.stdout and "Commands:" not in result.stdout
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "arguments",
    [["install", "apply"], ["projects", "list"], ["--vpn-config-file", "private"], ["--yes"]],
)
def test_removed_commands_cannot_mutate(arguments: list[str]) -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-m", "setup_system", *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2


def test_root_ui_is_rejected_before_opening(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["setup-system"])
    monkeypatch.setattr("os.geteuid", lambda: 0)
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
