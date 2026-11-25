"""Check server-side npm source restrictions and script suppression with an isolated lock."""

import base64
import json

import pytest
import setup_plugin_typescript_vue as tools
from setup_plugin_typescript_vue import Plugin, Settings, checked_lock


def test_resolution_and_ci_preserve_an_exact_lock(tmp_path, monkeypatch):
    artifact = {
        "version": "1.0.0",
        "resolved": "https://registry.npmjs.org/prettier/-/prettier-1.0.0.tgz",
        "integrity": "sha512-" + base64.b64encode(b"a" * 64).decode(),
    }
    calls = []

    def npm(argv, stage):
        calls.append(argv)
        assert "--ignore-scripts" in argv and "--no-audit" in argv
        if "install" in argv:
            doc = json.loads((stage / "package.json").read_text())
            (stage / "package-lock.json").write_text(
                json.dumps(
                    {"lockfileVersion": 3, "packages": {"": doc, "node_modules/prettier": artifact}}
                )
            )

    monkeypatch.setattr(tools, "execute", npm)
    plugin = Plugin()
    lock = plugin.lock(tmp_path, Settings())
    folder = tmp_path / "generation"
    folder.mkdir()
    plugin.install_tools(tmp_path, folder, lock)
    assert calls[-1][1] == "ci" and (folder / "package-lock.json").read_bytes() == lock
    with pytest.raises(ValueError):
        checked_lock(lock.replace(b"registry.npmjs.org", b"example.invalid"))
    with pytest.raises(ValueError):
        checked_lock(lock.replace(b"node_modules/prettier", b"../prettier"))
    assert "prettier" not in plugin.commands(Settings(prettier=False))


@pytest.mark.parametrize("fault", ["host", "query", "short", "weak", "filename", "symlink"])
def test_npm_rejects_unverifiable_artifact(fault):
    ref = {
        "resolved": "https://registry.npmjs.org/@vue/language-server/-/language-server-3.0.0.tgz",
        "integrity": "sha512-" + base64.b64encode(b"x" * 64).decode(),
        "version": "3.0.0",
    }
    if fault == "host":
        ref["resolved"] = ref["resolved"].replace("registry.npmjs.org", "foreign.invalid")
    if fault == "query":
        ref["resolved"] += "?redirect"
    if fault == "short":
        ref["integrity"] = "sha512-AQ=="
    if fault == "weak":
        ref["integrity"] = ref["integrity"].replace("sha512", "sha1")
    if fault == "filename":
        ref["resolved"] = ref["resolved"].replace("3.0.0", "3.0.1")
    if fault == "symlink":
        ref["link"] = True
    lock = {
        "lockfileVersion": 3,
        "packages": {"": {"name": "toolchain"}, "node_modules/@vue/language-server": ref},
    }
    with pytest.raises(ValueError):
        checked_lock(json.dumps(lock).encode())
