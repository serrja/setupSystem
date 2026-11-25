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
