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


@pytest.mark.parametrize(
    "message",
    [b"not-json", b'{"plugin_id":"not-present","method":"validate"}', b"x" * 524289],
    ids=["malformed", "uninstalled", "oversized"],
)
def test_invalid_worker_messages_fail_closed_without_producing_output(tmp_path, message):
    result = subprocess.run(
        [sys.executable, "-I", "-m", "setup_system_sdk.worker"],
        cwd=tmp_path,
        input=message,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2 and result.stdout == result.stderr == b""
    assert not list(tmp_path.iterdir())


def test_wheel_version_and_short_help_are_read_only(monkeypatch):
    from importlib.metadata import version

    from typer.testing import CliRunner

    runner = CliRunner()
    assert runner.invoke(entrypoint.cli, ["--version"]).stdout.strip() == version("setup-system")
    assert "Textual" in runner.invoke(entrypoint.cli, ["-h"]).stdout


def test_run_forwards_application_options_and_literal_paths_only(tmp_path, monkeypatch):
    import os
    import shutil

    from typer.testing import CliRunner

    root = Path(__file__).resolve().parents[1]
    shutil.copyfile(root / "Makefile", tmp_path / "Makefile")
    shutil.copytree(root / "mk", tmp_path / "mk")
    (tmp_path / "libexec").mkdir()
    (tmp_path / "libexec/run.sh").write_text(
        'printf "%s\\0" "$SETUP_SYSTEM_SUGGESTION_RESULTS" "$XDG_CONFIG_HOME" '
        '"${SETUP_SYSTEM_RESULT-unset}" > received\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(entrypoint, "resource_root", lambda: tmp_path)
    path = "/tmp/配置 '$(MAKE_VERSION)'\n-literal"
    monkeypatch.setenv("XDG_CONFIG_HOME", path)
    monkeypatch.setenv("SETUP_SYSTEM_SUGGESTION_RESULTS", "9")
    monkeypatch.setenv("SETUP_SYSTEM_RESULT", "/caller/result")
    monkeypatch.setenv("MAKEFILES", "/unknown/file.mk")

    def run(executable, args, env):
        subprocess.run(args, executable=executable, env=env, check=True)

    monkeypatch.setattr(os, "execve", run)
    result = CliRunner().invoke(entrypoint.cli, [])
    assert result.exit_code == 0, result.exception
    assert (tmp_path / "received").read_bytes().split(b"\0") == [b"9", path.encode(), b"unset", b""]


def test_launcher_validates_numeric_preferences_before_make(monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.setenv("SETUP_SYSTEM_SUGGESTION_RESULTS", "invalid-private-value")
    monkeypatch.setattr(entrypoint.os, "execve", lambda *args: pytest.fail("unexpected launcher"))
    result = CliRunner().invoke(entrypoint.cli, [])
    assert result.exit_code == 1
    assert "application settings" in result.output and "invalid-private-value" not in result.output
