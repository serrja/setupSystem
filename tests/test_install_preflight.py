"""Unsupported root execution and resource origins stop before mutation."""

import pytest

from setup_system.infrastructure.privileged import PrivilegedExecutor
from setup_system.infrastructure.trusted_bootstrap import validate_url


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a",
        "https://evil.invalid/a",
        "https://user:secret@github.com/a",
        "https://github.com:8443/a",
    ],
)
def test_privileged_sources_require_allowlisted_direct_https(url: str) -> None:
    with pytest.raises(ValueError):
        validate_url(url)


def test_ui_does_not_invoke_sudo_when_it_is_already_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.geteuid", lambda: 0)
    monkeypatch.setattr(
        "subprocess.run", lambda *args, **kwargs: pytest.fail("Root UI reached sudo")
    )
    with pytest.raises(ValueError, match="unprivileged"):
        PrivilegedExecutor("v1.2.3", "a" * 40).run("base", {})


def test_dangerous_action_is_not_a_system_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    with pytest.raises(ValueError, match="Unexpected"):
        PrivilegedExecutor("v1.2.3", "a" * 40).run("shell", {"command": "id"})
