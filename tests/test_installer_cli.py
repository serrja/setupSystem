"""The retired shell and public automation frontends cannot bypass Textual consent."""

from pathlib import Path

from setup_system.application.installer import InstallRequest, Operation
from setup_system.domain.settings import Section


def test_request_is_an_immutable_plan_input() -> None:
    from dataclasses import FrozenInstanceError

    import pytest

    request = InstallRequest(Operation.PLAN)
    with pytest.raises(FrozenInstanceError):
        request.section = Section.WORKSPACE
    assert request.yes is False


def test_bootstrap_does_not_elevate_the_python_ui() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "install.sh").read_text()
    assert 'exec sudo -- "$executable"' not in source
    assert 'exec runuser -u "$target"' in source
    assert 'exec env HOME="$target_home" USER="$target" LOGNAME="$target"' in source
