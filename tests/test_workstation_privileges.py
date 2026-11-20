"""User profile writes never inherit privileged ownership or execute user code as root."""

from pathlib import Path

import pytest

from setup_system.application import workstation
from setup_system.infrastructure.user_files import apply_files


@pytest.mark.parametrize(
    "operation", [lambda: workstation.apply_desktop(Path("/missing")), lambda: apply_files({})]
)
def test_root_user_configuration_is_rejected_before_writes(
    monkeypatch: pytest.MonkeyPatch, operation
) -> None:
    monkeypatch.setattr("os.geteuid", lambda: 0)
    with pytest.raises(ValueError, match="root"):
        operation()
