"""Run a synthetic tool manager through the SDK without importing private host settings."""

import json
import sys

import pytest

from setup_system_sdk.toolchain import execute


@pytest.mark.parametrize(
    "injection", ["UV_INDEX_URL", "PYTHONPATH", "NPM_CONFIG_REGISTRY", "BASH_ENV", "LD_PRELOAD"]
)
def test_tool_manager_excludes_foreign_configuration(tmp_path, monkeypatch, injection):
    monkeypatch.setenv(injection, "untrusted.invalid")
    script = tmp_path / "probe.py"
    script.write_text("import os,json; print(json.dumps(dict(os.environ)))")
    output = execute([sys.executable, "-I", str(script)], tmp_path, capture=True)
    env = json.loads(output)
    assert env["HOME"] == env["TMPDIR"] == str(tmp_path)
    assert "untrusted.invalid" not in env.values()
    assert env["NPM_CONFIG_USERCONFIG"] != env["NPM_CONFIG_GLOBALCONFIG"]
    assert env["NPM_CONFIG_ENGINE_STRICT"] == "true" and env["UV_NO_CONFIG"] == "1"
    assert env["PATH"] == "/usr/bin:/bin"
