"""Keep Docker's native login bound to the credential helper reviewed in its plan."""

import asyncio
import json
from pathlib import Path

import pytest
import setup_plugin_docker as app
from setup_plugin_docker import registry

from setup_system_sdk import Mutation, Outcome
from setup_system_sdk.native import command


@pytest.mark.parametrize(
    ("host", "terminal_rc", "publish", "outcome"),
    [
        ("docker.io", 0, True, Outcome.CHANGED),
        ("private.example.test:5000", 0, False, Outcome.NEEDS_USER_ACTION),
        ("private.example.test:5000", 1, True, Outcome.NEEDS_USER_ACTION),
    ],
)
def test_login_uses_only_confirmed_store(
    tmp_path, monkeypatch, host, terminal_rc, publish, outcome
):
    monkeypatch.setenv("HOME", str(tmp_path))
    docker = tmp_path / "docker"
    docker.touch()
    monkeypatch.setattr(registry, "DOCKER", docker)
    monkeypatch.setattr(registry.user_terminal, "available", lambda: True)
    cfg = tmp_path / ".docker/config.json"
    cfg.parent.mkdir()
    cfg.write_text('{"credsStore":"secretservice"}')
    origin = "https://index.docker.io/v1/" if host == "docker.io" else host
    entries = {}
    stage = []

    def helper(*argv, **kw):
        if argv[0] == "/usr/bin/jq":
            return command(*argv, **kw)
        assert argv == ("/usr/bin/docker-credential-secretservice", "list")
        return 0, json.dumps(entries)

    def terminal(argv):
        assert argv[:2] == [str(docker), "--config"]
        assert argv[3:] == (["login"] if host == "docker.io" else ["login", host])
        folder = Path(argv[2])
        assert folder.stat().st_mode & 0o077 == 0
        assert json.loads((folder / "config.json").read_bytes()) == {"credsStore": "secretservice"}
        stage.append(folder)
        if publish:
            entries[origin] = "fixture"
        return terminal_rc

    monkeypatch.setattr(registry, "command", helper)
    monkeypatch.setattr(registry.user_terminal, "run_native", terminal)
    plugin, settings, inputs = app.Plugin(), app.Settings(), registry.SignIn(host=host)
    plan = plugin.prepare("login", settings, inputs)
    assert plan.outcome == Outcome.READY
    result = asyncio.run(
        plugin.apply_request(
            "login",
            Mutation(
                action_id="login",
                inputs=inputs.model_dump(),
                settings={},
                plan=plan,
            ),
        )
    )
    assert result.outcome == outcome
    assert len(stage) == 1 and not stage[0].exists()
    assert asyncio.run(plugin.verify(plan, settings, inputs)).outcome == (
        Outcome.COMPLETE if publish else Outcome.NEEDS_USER_ACTION
    )
    if publish:
        assert plugin.prepare("login", settings, inputs).outcome == Outcome.NO_CHANGES


def test_login_requires_the_actual_selected_registry_helper(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / ".docker/config.json"
    source.parent.mkdir()
    selected = registry.SignIn()
    # Docker Hub uses its canonical index URL when choosing the credential store.
    source.write_text('{"credHelpers":{"docker.io":"pass"}}')
    assert registry.login_plan(selected).outcome == Outcome.NEEDS_USER_ACTION
    source.write_text('{"credsStore":"pass","auths":{"docker.io":{"auth":"fixture-only"}}}')
    result = registry.login_plan(selected)
    assert result.outcome == Outcome.NEEDS_USER_ACTION and "fixture-only" not in result.summary
    monkeypatch.setattr(registry.user_terminal, "available", lambda: False)
    result = registry.login_plan(selected, ("pass", "absent"))
    assert (
        result.outcome == Outcome.NEEDS_USER_ACTION and "docker login docker.io" in result.summary
    )
