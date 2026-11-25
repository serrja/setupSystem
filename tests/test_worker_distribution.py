"""Exercise the worker protocol against installed PEP 621 metadata and real resource files."""

import asyncio
import io
import json
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fixtures.choice_worker import Preferences, Select
from test_extensions import descriptor, wheel

from setup_system_sdk import Action, Manifest, Outcome, Result, worker
from setup_system_sdk.protocol import LIMIT, Reply, Request


@pytest.fixture
def installed_worker(tmp_path, monkeypatch):
    name = "reviewkit"
    package = "ext_reviewkit"
    manifest = descriptor(
        name,
        entry_point=package + ":Toy",
        actions=[Action(id="select", label="Select test item").model_dump(mode="json")],
        settings_schema=Preferences.model_json_schema(),
        environment_fields=Preferences.environment_fields,
        input_schemas={"select": Select.model_json_schema()},
    )
    reviewed = Manifest.model_validate(manifest)
    archive = wheel(
        tmp_path,
        "independent-fixture",
        "1.0",
        package,
        (Path(__file__).parent / "fixtures/choice_worker.py").read_text(),
        manifest=reviewed.model_dump(mode="json"),
    )
    path = tmp_path / "reviewed"
    subprocess.run(
        [
            "uv",
            "--offline",
            "pip",
            "install",
            "--target",
            str(path),
            "--no-deps",
            "--no-index",
            str(archive),
        ],
        check=True,
    )
    monkeypatch.syspath_prepend(str(path))
    try:
        yield name, path / package / "manifest.json"
    finally:
        sys.modules.pop(package, None)


def test_worker_methods_revalidate_installed_schema_inputs_and_observed_choices(
    installed_worker, monkeypatch
):
    plugin, manifest = installed_worker

    def call(method, **parameters):
        return asyncio.run(worker.dispatch(Request(plugin_id=plugin, method=method, **parameters)))

    monkeypatch.setenv("SETUP_PLUGIN_REVIEWKIT_COUNT", "4")
    assert call("validate", profile={"count": 2}, saved={"count": 3}).settings == {"count": 4}
    assert call("validate", overrides={"count": 5}).settings == {"count": 5}
    assert call("migrate", saved={"count": 2}).settings == {"count": 2}
    with pytest.raises(ValueError, match="migration"):
        call("migrate", settings_version=2)
    assert call("inspect").result.outcome == Outcome.READY
    choices = call("choices", provider="items").choices
    assert choices[0] == "existing" and len(choices) <= 128 and all(len(s) <= 512 for s in choices)
    args = {"action_id": "select", "inputs": {"item": "existing"}}
    plan = call("plan", **args).plan
    assert call("verify", plan=plan, **args).result.outcome == Outcome.NO_CHANGES
    assert call("plan", action_id="select").plan == plan
    with pytest.raises(ValueError, match="no longer available"):
        call("plan", action_id="select", inputs={"item": "old"})
    with pytest.raises(ValueError, match="Missing action"):
        call("plan")
    with pytest.raises(ValueError, match="Invalid operation"):
        call("verify", **args)
    with pytest.raises(ValueError):
        call("validate", overrides={"count": 100})
    damaged = json.loads(manifest.read_bytes())
    damaged["settings_schema"]["title"] = "Different reviewed schema"
    manifest.write_text(json.dumps(damaged))
    with pytest.raises(ValueError, match="schema differs"):
        worker.load_backend(plugin)


def test_worker_framing_suppresses_exception_payloads(installed_worker, monkeypatch, capsysbinary):
    plugin, _ = installed_worker

    def invoke(data):
        monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(data)))
        status = worker.main()
        captured = capsysbinary.readouterr()
        return status, captured.out, captured.err

    status, output, errors = invoke(
        Request(plugin_id=plugin, method="inspect").model_dump_json().encode()
    )
    assert status == 0 and Reply.model_validate_json(output).result.outcome == Outcome.READY
    assert not errors
    for payload in (
        b"x" * (LIMIT + 1),
        b'{"private":"private-value"}',
        b'{"plugin_id":"missing","method":"inspect"}',
    ):
        assert invoke(payload) == (2, b"", b"")


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "entry", sorted((ROOT / "plugins").glob("*/*/src/*/action.py")), ids=lambda p: p.parent.name
)
def test_every_normal_user_make_recipe_refuses_invalid_wire_input(entry, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(entry.parents[1]))
    monkeypatch.setattr(sys, "argv", [str(entry), "fixture"])
    monkeypatch.setattr(
        sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b'{"private":"sensitive-fixture-marker"}'))
    )
    runpy.run_path(str(entry), run_name="__main__")
    captured = capsys.readouterr()
    assert Result.model_validate_json(captured.out).outcome == Outcome.BLOCKED
    assert "sensitive-fixture-marker" not in (captured.out + captured.err)
