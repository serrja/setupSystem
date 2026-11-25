import json
from pathlib import Path

import pytest
from setup_plugin_firefox import policy

from setup_system_sdk import Outcome


def test_preserve_unrelated_policies_and_require_extension_consent() -> None:
    before = b'{"policies":{"Homepage":{"URL":"https://example.test"}}}'
    settings = policy.Settings()
    after = policy.render(before, settings)
    actual = json.loads(after)["policies"]
    assert actual["Homepage"]["URL"] == "https://example.test"
    assert "ExtensionSettings" not in actual
    assert actual["Preferences"]["dom.security.https_only_mode"]["Value"] is True
    assert policy.render(after, settings) == after
    extra = json.loads(policy.render(before, policy.Settings(install_ublock=True)))["policies"]
    assert policy.EXTENSION_ID in extra["ExtensionSettings"]
    with pytest.raises(ValueError, match="locked"):
        policy.render(
            b'{"policies":{"Preferences":{"browser.contentblocking.category":{"Status":"locked"}}}}',
            settings,
        )


def test_policy_ready_changed_no_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "etc/firefox/policies/policies.json"
    path.parent.mkdir(parents=True)
    path.parent.chmod(0o755)
    path.parent.parent.chmod(0o755)
    monkeypatch.setattr(policy, "PATH", path)
    monkeypatch.setattr(policy, "current_target", lambda: ("amd64", True))
    monkeypatch.setattr(
        policy,
        "snapshot",
        lambda p: (p.read_bytes(), p.stat().st_mode & 0o777) if p.exists() else (None, None),
    )
    settings = policy.Settings()
    first = policy.preview(settings)
    assert first.outcome == Outcome.READY and first.resources[0].before is None
    path.write_bytes(policy.render(b"", settings))
    path.chmod(0o644)
    assert policy.preview(settings).outcome == Outcome.NO_CHANGES
    path.chmod(0o600)
    assert policy.preview(settings).outcome == Outcome.READY


def test_confirmed_native_policy_rechecks_and_preserves_existing_profiles(tmp_path, monkeypatch):
    import asyncio

    import setup_plugin_firefox as firefox

    from setup_system_sdk import Inputs

    path = tmp_path / "etc/firefox/policies/policies.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"policies":{"Homepage":{"URL":"https://fixture.test"}}}')
    path.parent.chmod(0o755)
    path.parent.parent.chmod(0o755)

    def snapshot(p):
        return p.read_bytes(), p.stat().st_mode & 0o777

    monkeypatch.setattr(policy, "PATH", path)
    monkeypatch.setattr(policy, "snapshot", snapshot)
    monkeypatch.setattr(policy, "current_target", lambda: ("amd64", True))
    settings = policy.Settings()
    plugin = firefox.Plugin()
    inputs = Inputs()

    def run(plugin_id, playbook, extra, method):
        assert (plugin_id, playbook, method) == ("firefox", "policy.yml", "sudo")
        parsed = policy.Authorization.model_validate(extra)
        assert parsed.approval.settings == settings
        approvals.append(parsed.approval)

    approvals = []
    monkeypatch.setattr(firefox, "run_native_play", run)
    first = asyncio.run(plugin.plan("configure", settings, inputs))
    firefox.apply_native("configure", settings, inputs, first)
    assert len(approvals) == 1
    with pytest.raises(ValueError, match="authenticated"):
        policy.apply(approvals[0])
    # Redirect the privilege boundary and filesystem operations to this synthetic policy tree.
    from types import SimpleNamespace

    monkeypatch.setattr(policy, "os", SimpleNamespace(geteuid=lambda: 0))
    monkeypatch.setattr(policy, "require_installed_resource", lambda path, identifier: None)
    publish = policy.publish
    monkeypatch.setattr(
        policy,
        "publish",
        lambda checked, options: publish(checked, options, backups=tmp_path / "backup"),
    )
    policy.apply(approvals[0])
    assert json.loads(path.read_text())["policies"]["Homepage"]["URL"] == "https://fixture.test"
    assert asyncio.run(plugin.verify(first, settings, inputs)).outcome == Outcome.COMPLETE
    assert asyncio.run(plugin.plan("configure", settings, inputs)).outcome == Outcome.NO_CHANGES
    path.write_text('{"policies":{"Homepage":{"URL":"https://owner.invalid"}}}')
    with pytest.raises(ValueError, match="changed after confirmation"):
        policy.apply(approvals[0])
    assert asyncio.run(plugin.verify(first, settings, inputs)).outcome == Outcome.PARTIAL


def test_remove_exact_generated_policy_preserves_modified_content(tmp_path, monkeypatch):
    import asyncio
    import hashlib

    import setup_plugin_firefox as firefox

    from setup_system_sdk import Inputs, Resource, ownership_context

    path = tmp_path / "etc/firefox/policies/policies.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(policy.render(b"", policy.Settings()))
    path.chmod(0o644)
    sibling = path.parent / "admin.json"
    sibling.write_text("keep")
    monkeypatch.setattr(policy, "PATH", path)

    def bytes_and_mode(p):
        return (p.read_bytes(), p.stat().st_mode & 0o777) if p.exists() else (None, None)

    monkeypatch.setattr(policy, "snapshot", bytes_and_mode)
    row = Resource(
        kind="file",
        identity=str(path),
        ownership="created",
        after=hashlib.sha256(path.read_bytes()).hexdigest(),
        permissions=0o644,
        cleanup=True,
    )
    assert policy.removal().outcome == Outcome.NO_CHANGES
    with ownership_context((row,)):
        plugin = firefox.Plugin()
        plan = plugin.prepare("remove-policy", policy.Settings(), Inputs())
        original = path.read_bytes()
        path.write_text('{"policies":{"Homepage":{}}}')
        assert policy.removal().outcome == Outcome.BLOCKED
        with pytest.raises(ValueError, match="differs"):
            policy.remove_generated(plan)
        path.write_bytes(original)
        policy.remove_generated(plan)
        assert not path.exists() and sibling.read_text() == "keep"
        assert (
            asyncio.run(plugin.verify(plan, policy.Settings(), Inputs())).outcome
            == Outcome.COMPLETE
        )
