"""Keep bundled instructions tied to the reviewed engineering upstream."""

import hashlib
import tomllib
from pathlib import Path

from setup_plugin_codex import Plugin, Project, Settings

from setup_system.layout import resource_root

ASSETS = Path(__import__("setup_plugin_codex").__file__).parent / "resources"


def test_release_policy_pin_matches_repository_instructions() -> None:
    baseline = tomllib.loads((ASSETS / "rules.toml").read_text())
    assert (
        f"/blob/{baseline['commit']}/{baseline['document']}"
        in (resource_root() / "AGENTS.md").read_text()
    )
    raw = (ASSETS / "engineering.md").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == baseline["sha256"]


def test_existing_override_and_personal_settings_survive(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".codex"
    root.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(root))
    config = root / "config.toml"
    config.write_text('# owner preference\nmodel="owner-selected"\n')
    notes = root / "AGENTS.override.md"
    notes.write_text("Lead\n\n# >>> setupSystem >>>\nOld\n# <<< setupSystem <<<\nEnd\n")
    personal = root / "AGENTS.md"
    personal.write_text("lower priority")
    (root / "auth.json").symlink_to(tmp_path / "never-open")
    desired = Plugin().files("configure", Settings(instructions=True), Project())
    assert b"owner-selected" in desired[config] and b"# owner preference" in desired[config]
    assert (
        b"Lead" in desired[notes] and b"End" in desired[notes] and b"\nOld\n" not in desired[notes]
    )
    assert (ASSETS / "engineering.md").read_bytes().strip() in desired[notes]
    assert personal not in desired and root / "auth.json" not in desired
