"""Verify immutable engineering rule provenance for Codex."""

import pytest


@pytest.mark.parametrize(
    "change",
    [
        {"commit": "main"},
        {"document": "../policy.md"},
        {"document": "/policy.md"},
        {"sha256": "bad"},
    ],
)
def test_policy_metadata_rejects_mutable_or_unsafe_provenance(change: dict[str, str]) -> None:
    from setup_plugin_codex import PolicySource

    policy = {
        "repository": "serrja/baseRules",
        "commit": "a" * 40,
        "document": "docs/engineering.md",
        "sha256": "b" * 64,
        **change,
    }
    with pytest.raises(ValueError):
        PolicySource.model_validate(policy)
