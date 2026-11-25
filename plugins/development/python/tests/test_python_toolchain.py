"""Verify wheel-only selection and the complete tool lifecycle in a synthetic HOME."""

import asyncio
import hashlib
from pathlib import Path

import pytest
from setup_plugin_python import Plugin, Settings, requirements

from setup_system_sdk import Inputs, Outcome, ownership_context


def test_checked_pypi_sha_and_origin():
    filename = "ruff-1.0-py3-none-any.whl"
    url = "https://files.pythonhosted.org/packages/aa/bb/cdef/" + filename
    sha = "3" * 64
    doc = f'''[[packages]]
name="ruff"
version="1.0"
wheels=[{{url="{url}", hashes={{sha256="{sha}"}}}}]
'''.encode()
    assert requirements(doc) == f"ruff @ {url} --hash=sha256:{sha}\n".encode()
    with pytest.raises(ValueError):
        requirements(doc.replace(b"files.pythonhosted.org", b"external.invalid"))
    with pytest.raises(ValueError):
        requirements(doc.replace(sha.encode(), b"bad"))
    with pytest.raises(ValueError):
        requirements(doc.replace(b"py3-none-any", b"cp37-cp37-win32"))
    with pytest.raises(ValueError):
        requirements(doc.replace(b"-1.0-", b"-1.1-"))


def test_native_tool_generation_and_safe_removal(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("DATA", "CONFIG", "STATE", "CACHE"):
        monkeypatch.setenv("XDG_" + name + "_HOME", str(tmp_path / name.lower()))
    plugin = Plugin()
    opts = Settings(type_checker=False)
    lock = b"synthetic pinned release"
    monkeypatch.setattr(plugin, "lock", lambda stage, settings: lock)

    def install(stage, folder, chosen):
        assert chosen == lock
        path = folder / "bin/ruff"
        path.parent.mkdir()
        path.write_bytes(b"#!/bin/sh\nexit 0\n")
        path.chmod(0o755)

    monkeypatch.setattr(plugin, "install_tools", install)
    plan = plugin.prepare("configure", opts, Inputs())
    assert plan.outcome == Outcome.READY and not list(tmp_path.iterdir())
    result = plugin.mutate("configure", opts, Inputs(), plan)
    assert result.outcome == Outcome.CHANGED
    assert asyncio.run(plugin.verify(plan, opts, Inputs())).outcome == Outcome.COMPLETE
    folder = Path(plan.resources[0].identity)
    assert folder.name == hashlib.sha256(lock).hexdigest()
    with ownership_context(tuple(result.resources)):
        assert plugin.prepare("check", opts, Inputs()).outcome == Outcome.NO_CHANGES
        assert plugin.prepare("configure", opts, Inputs()).outcome == Outcome.NO_CHANGES
        unexpected = folder / "user data"
        unexpected.write_text("preserve")
        with pytest.raises(ValueError, match="changed"):
            plugin.prepare("remove-tools", opts, Inputs())
        assert unexpected.read_text() == "preserve"
        unexpected.unlink()
        cleanup = plugin.prepare("remove-tools", opts, Inputs())
        assert cleanup.outcome == Outcome.READY
        assert plugin.mutate("remove-tools", opts, Inputs(), cleanup).outcome == Outcome.CHANGED
        assert asyncio.run(plugin.verify(cleanup, opts, Inputs())).outcome == Outcome.COMPLETE
    assert not folder.exists()


def test_uv_installs_the_exact_local_wheel_and_refuses_changed_hash(tmp_path):
    from subprocess import check_output

    from test_extensions import wheel

    # An offline fixture exercises uv itself with the same hashed direct-reference contract.
    entrypoints = b"[console_scripts]\nruff=fixture_tool:run\n"
    distribution = wheel(
        tmp_path,
        "fixture-tool",
        "1.0",
        "fixture_tool",
        'def run(): print("fixture 1.0")',
        extras={
            "fixture_tool-1.0.dist-info/entry_points.txt": entrypoints,
        },
    )
    digest = hashlib.sha256(distribution.read_bytes()).hexdigest()
    lock = f"fixture-tool @ {distribution.as_uri()} --hash=sha256:{digest}\n".encode()
    backend = Plugin()
    release = tmp_path / "generation"
    backend.install_tools(tmp_path, release, lock)
    assert backend.readback(release, Settings(type_checker=False))
    assert check_output([str(release / "bin/ruff")], text=True).strip() == "fixture 1.0"
    with pytest.raises(ValueError, match="tool manager"):
        backend.install_tools(
            tmp_path, tmp_path / "different", lock.replace(digest.encode(), b"0" * 64)
        )
