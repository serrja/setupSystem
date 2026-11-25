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


def test_private_file_help_and_unknown_operation_do_not_open_requests(monkeypatch):
    from typer.testing import CliRunner

    from setup_system.file_tasks import cli

    monkeypatch.delenv("SETUP_SYSTEM_REQUEST", raising=False)
    runner = CliRunner()
    assert runner.invoke(cli, ["--help"]).exit_code == 0
    invalid = runner.invoke(cli, ["arbitrary-operation"])
    assert invalid.exit_code == 2
    assert invalid.stdout == ""


def test_private_file_preview_emits_one_json_record(tmp_path, monkeypatch):
    import json

    from typer.testing import CliRunner

    from setup_system.file_tasks import cli

    request = tmp_path / "request.json"
    request.write_text('{"schema":1,"settings":{},"expected":null}')
    (tmp_path / "files").mkdir()
    monkeypatch.setenv("SETUP_SYSTEM_REQUEST", str(request))
    monkeypatch.setenv("SETUP_SYSTEM_RESULT", str(tmp_path / "result.json"))
    result = CliRunner().invoke(cli, ["preview"])
    assert result.exit_code == 0, result.exception
    assert result.stdout.endswith("\n") and result.stdout.count("\n") == 1
    assert json.loads(result.stdout) == {"changes": [], "before": {}, "after": {}}
    assert result.stderr == ""


@pytest.mark.parametrize("target", ["not-a-make-target", "admin-apply"])
def test_project_helper_rejects_foreign_targets_before_reading_files(target, monkeypatch):
    from typer.testing import CliRunner

    from setup_system.project_tasks import cli

    monkeypatch.delenv("SETUP_SYSTEM_REQUEST", raising=False)
    result = CliRunner().invoke(cli, [target])
    assert result.exit_code == 2 and not result.stdout


def test_wheel_version_and_short_help_are_read_only(monkeypatch):
    from importlib.metadata import version

    from typer.testing import CliRunner

    runner = CliRunner()
    assert runner.invoke(entrypoint.cli, ["--version"]).stdout.strip() == version("setup-system")
    assert "Textual" in runner.invoke(entrypoint.cli, ["-h"]).stdout
