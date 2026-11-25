"""The retired shell and public automation frontends cannot bypass Textual consent."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from setup_system.plugin_manager import Snapshot
from setup_system_sdk import Inputs, Mutation, Plan


def test_request_is_an_immutable_plan_input() -> None:
    values = {"title": "prepared"}
    snapshot = Snapshot(
        digest="f" * 64,
        plugin_id="example",
        mutation=Mutation(
            settings=values, inputs={}, action_id="configure", plan=Plan(summary="Preview")
        ),
    )
    values["title"] = "later"
    assert snapshot.mutation.settings == {"title": "prepared"}
    with pytest.raises(ValidationError, match="frozen"):
        snapshot.plugin_id = "another"
    with pytest.raises(ValidationError):
        Inputs.model_validate({"yes": True})


def test_bootstrap_does_not_elevate_the_python_ui() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "install.sh").read_text()
    assert 'exec sudo -- "$executable"' not in source
    run = source.split("    run)", 1)[1].split(";;", 1)[0]
    assert "id -u" in run and "!= 0" in run
    assert "exec /usr/bin/setup-system" in run and "sudo " not in run
