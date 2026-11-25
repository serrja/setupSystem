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


@pytest.mark.parametrize("fd", [1, 2])
def test_native_command_drains_excess_output_without_publishing_it(monkeypatch, fd):
    from setup_system.infrastructure import processes

    monkeypatch.setattr(processes, "MAX_OUTPUT", 1024)
    monkeypatch.setattr(
        processes.os, "killpg", lambda *args: pytest.fail("Signalled an already reaped group")
    )
    program = f"import os; os.write({fd}, b'x' * 200000)"
    with pytest.raises(subprocess.SubprocessError, match="exceeds the supported size"):
        run_command([sys.executable, "-I", "-c", program], timeout=10)


def test_binary_git_output_remains_exact_and_uncaptured_output_is_discarded(monkeypatch):
    from setup_system.infrastructure import processes

    monkeypatch.setattr(processes, "MAX_OUTPUT", 3)
    argv = [sys.executable, "-I", "-c", "import os; os.write(1,bytes([255,13,10]))"]
    assert run_command(argv, text=False).stdout == bytes([255, 13, 10])
    monkeypatch.setattr(processes, "MAX_OUTPUT", 1)
    result = run_command(argv, discard_output=True)
    assert result.stdout == result.stderr == ""


def test_text_mode_preserves_native_newline_translation():
    argv = [sys.executable, "-I", "-c", "import os; os.write(1,bytes([120,13,10,121,13]))"]
    assert run_command(argv).stdout == "x\ny\n"


def test_repeated_termination_during_grace_still_reaps_the_child(tmp_path):
    import os
    import signal
    import time

    ready, terminating = tmp_path / "ready", tmp_path / "terminating"
    child = """
import os, signal, sys, time
from pathlib import Path
ready, terminating = map(Path, sys.argv[1:])
signal.signal(signal.SIGTERM, lambda *_: terminating.touch())
ready.write_text(str(os.getpid()))
time.sleep(20)
"""
    parent = """
import sys
from setup_system.infrastructure.processes import run_command
try:
    run_command([sys.executable, "-I", "-c", *sys.argv[1:]], termination_grace=1)
except KeyboardInterrupt:
    sys.exit(42)
"""

    def wait_for(path):
        deadline = time.monotonic() + 5
        while not path.exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)

    with subprocess.Popen([sys.executable, "-I", "-c", parent, child, ready, terminating]) as owner:
        try:
            wait_for(ready)
            owner.send_signal(signal.SIGTERM)
            wait_for(terminating)
            owner.send_signal(signal.SIGTERM)
            assert owner.wait(timeout=5) == 42
            with pytest.raises(ProcessLookupError):
                os.kill(int(ready.read_text()), 0)
        finally:
            if owner.poll() is None:
                owner.kill()


def test_larger_probe_keeps_its_cwd_and_ignores_unneeded_stderr(tmp_path, monkeypatch):
    from setup_system.infrastructure import processes

    monkeypatch.setattr(processes, "MAX_OUTPUT", 3)
    cwd = tmp_path / "Unicode проба"
    cwd.mkdir()
    program = "import os; os.write(2, b'x' * 200000); os.write(1, os.fsencode(os.getcwd()))"
    command = [sys.executable, "-I", "-c", program]
    result = run_command(command, cwd=cwd, discard_stderr=True, max_output_bytes=4096)
    assert result.stdout == str(cwd)
    assert result.stderr == ""
    with pytest.raises(subprocess.SubprocessError, match="exceeds the supported size"):
        run_command(command, cwd=cwd, discard_stderr=True)
