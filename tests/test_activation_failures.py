"""Keep environment failure diagnostics useful without exposing child output."""

import asyncio
import sys
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

from setup_system.extensions import EnvironmentStep, _run


@pytest.mark.parametrize("step", list(EnvironmentStep))
def test_activation_failure_identifies_stage_and_exit_without_native_output(tmp_path, capfd, step):
    code = "import sys; print('fixture-private-token', file=sys.stderr); sys.exit(23)"
    with pytest.raises(ValueError) as failure:
        asyncio.run(_run([sys.executable, "-I", "-c", code], tmp_path, step))
    assert str(failure.value) == f"Plugin activation failed while {step.value} (exit 23)"
    streams = capfd.readouterr()
    assert "fixture-private-token" not in streams.out + streams.err + str(failure.value)


def test_git_accepts_the_debian_13_cryptography_runtime():
    project = Path(__file__).resolve().parents[1] / "plugins/applications/git/pyproject.toml"
    dependencies = tomllib.loads(project.read_text())["project"]["dependencies"]
    [requirement] = [
        item for raw in dependencies if (item := Requirement(raw)).name == "cryptography"
    ]
    assert Version("43.0.0") in requirement.specifier
