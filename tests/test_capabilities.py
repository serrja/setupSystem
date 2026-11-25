"""Closed package capabilities and shared platform facts at both sides of become."""

import pytest
from pydantic import ValidationError
from setup_plugin_cleanup import native as cleanup

from setup_system.infrastructure import native_target
from setup_system_sdk.apt import Packages


@pytest.mark.parametrize(
    "names",
    [
        "foot",
        [None],
        [True],
        [""],
        [],
        ["git=1"],
        ["-pkg"],
        ["../pkg.deb"],
        ["foot", "foot"],
        ["a:amd64"],
        ["MixedCase"],
        ["waybar/testing"],
    ],
)
def test_package_capabilities_reject_arbitrary_tokens_and_duplicates(names):
    with pytest.raises(ValidationError):
        Packages.model_validate({"names": names})


def test_elevated_cleanup_requires_supported_target_before_query(monkeypatch, tmp_path):
    assert cleanup.current_target is native_target.current_target
    monkeypatch.setattr(cleanup, "current_target", lambda: ("arm64", False))
    monkeypatch.setattr(
        cleanup, "inventory", lambda *args: pytest.fail("unsupported package query")
    )
    with pytest.raises(ValueError, match="Debian"):
        cleanup.preview("remove", ["nano"], 1000, tmp_path)
