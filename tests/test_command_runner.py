"""Process input is data and cannot compete with an existing stdin."""

import subprocess
import sys

import pytest

from setup_system.infrastructure.processes import run_command


def test_command_runner_delivers_text_input_without_executing_it() -> None:
    content = "fixture input with quotes ' and shell characters $(false)\n"
    result = run_command(
        [sys.executable, "-I", "-B", "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
        input=content,
        timeout=10,
    )
    assert result.returncode == 0 and result.stdout == content


def test_command_runner_refuses_competing_stdin_before_starting_a_process() -> None:
    with pytest.raises(ValueError, match="separate stdin"):
        run_command(["/does/not/exist"], input="fixture", stdin=subprocess.DEVNULL)
