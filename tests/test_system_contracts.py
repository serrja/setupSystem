"""System plans and historical receipts reject coercion at their input boundaries."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from setup_plugin_greeter.models import DesktopFile
from setup_plugin_plymouth.models import BootFile, BootReceipt
from setup_plugin_regional.models import RegionalReceipt

from setup_system.domain.system_facts import ClockSnapshot


@pytest.mark.parametrize("mode", [True, False, "420", 1.0, -1, 0o10000])
def test_file_mode_requires_a_bounded_integer(mode):
    with pytest.raises(ValidationError):
        DesktopFile.model_validate(
            dict(
                path=Path("/etc/fixture"), content=b"", mode=mode, observed=None, observed_mode=None
            )
        )


@pytest.mark.parametrize("value", [0, 1, "true", "false"])
def test_observed_flags_require_booleans(value):
    with pytest.raises(ValidationError):
        ClockSnapshot(timezone="UTC", can_ntp=value)
    with pytest.raises(ValidationError):
        RegionalReceipt(setting="keyboard", before=None, after="us", locale_gen_existed=value)
    with pytest.raises(ValidationError):
        BootFile(backup=None, mode=None, sha256=None, after="a" * 64, verified=value)


@pytest.mark.parametrize("version", [True, 1.0, "1"])
def test_boot_and_regional_receipt_versions_require_integers(version):
    with pytest.raises(ValidationError):
        BootReceipt.model_validate(
            {"kernel": "6.1", "files": {}, "status": "prepared", "schema_version": version}
        )
    with pytest.raises(ValidationError):
        RegionalReceipt.model_validate(
            {"setting": "timezone", "after": "UTC", "schema_version": version}
        )
