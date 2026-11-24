"""The retired shell and public automation frontends cannot bypass Textual consent."""

from pathlib import Path

from setup_system.models import BaseOptions, PlanSnapshot


def test_request_is_an_immutable_plan_input() -> None:
    import pytest
    from pydantic import ValidationError

    request = PlanSnapshot.capture(
        "base",
        BaseOptions().model_dump(mode="json"),
        {
            "status": "ready",
            "actions": ["Set timezone"],
            "observed": {"needs_system": True, "needs_user": False},
        },
        None,
    )
    with pytest.raises(ValidationError, match="frozen"):
        request.task = "workspace"
    with pytest.raises(ValidationError):
        BaseOptions.model_validate({"yes": True})


def test_bootstrap_does_not_elevate_the_python_ui() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "install.sh").read_text()
    assert 'exec sudo -- "$executable"' not in source
    run = source.split("    run)", 1)[1].split(";;", 1)[0]
    assert "id -u" in run and "!= 0" in run
    assert "exec /usr/bin/setup-system" in run and "sudo " not in run
