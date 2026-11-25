"""Only use synthetic client metadata for context and daemon tests."""

import asyncio
from pathlib import Path

import pytest
import setup_plugin_docker as docker

from setup_system_sdk import Inputs, Mutation, Outcome


def test_context_creation_does_not_contact_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values: dict[str, str] = {}
    calls: list[tuple[str, ...]] = []

    def command(*argv: str, capture: bool = False) -> tuple[int, str]:
        calls.append(argv)
        if argv[1:3] == ("context", "ls"):
            return 0, "\n".join(values)
        if argv[1:3] == ("context", "inspect"):
            return 0, values[argv[3]]
        assert argv[1:3] == ("context", "create")
        values[argv[3]] = argv[-1].removeprefix("host=")
        return 0, ""

    monkeypatch.setattr(docker, "command", command)
    plugin = docker.Plugin()
    selection = docker.Context(name="local-ci")
    settings = docker.Settings(mode="rootless")
    plan = asyncio.run(plugin.plan("create-context", settings, selection))
    assert plan.outcome == Outcome.READY and not values
    result = asyncio.run(
        plugin.apply_request(
            "create-context",
            Mutation(
                action_id="create-context",
                settings=settings.model_dump(mode="json"),
                inputs=selection.model_dump(mode="json"),
                plan=plan,
            ),
        )
    )
    assert result.outcome == Outcome.CHANGED
    assert asyncio.run(plugin.verify(plan, settings, selection)).outcome == Outcome.COMPLETE
    assert plugin.prepare("create-context", settings, selection).outcome == Outcome.NO_CHANGES
    assert (
        plugin.prepare("create-context", docker.Settings(mode="rootful"), selection).outcome
        == Outcome.BLOCKED
    )
    assert all(argv[1] == "context" for argv in calls)


def test_no_privilege_on_inactive_or_foreign_service(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = docker.Plugin()
    monkeypatch.setattr(docker, "command", lambda *a, **k: (1, ""))
    assert plugin.prepare("start-service", docker.Settings(), Inputs()).outcome == Outcome.BLOCKED
    assert (
        plugin.prepare("stop-service", docker.Settings(mode="rootful"), Inputs()).outcome
        == Outcome.BLOCKED
    )


def test_prune_previews_exact_digest_and_never_removes_a_volume(monkeypatch):
    digest = "sha256:" + "a" * 64
    state = {"dangling": True, "container": True}

    def run(*argv, capture=False):
        assert argv[1:3] == ("--host", docker.endpoint(docker.Settings())) and "volume" not in argv
        if "ls" in argv:
            return 0, digest if state["dangling"] else ""
        if "ps" in argv:
            return 0, "abc" if state["container"] else ""
        assert argv[-4:] == ("image", "rm", "--no-prune", digest)
        state["dangling"] = False
        return 0, ""

    monkeypatch.setattr(docker, "command", run)
    plugin = docker.Plugin()
    data = docker.Image(digest=digest)
    assert plugin.prepare("prune", docker.Settings(), data).outcome == Outcome.BLOCKED
    state["container"] = False
    plan = plugin.prepare("prune", docker.Settings(), data)
    assert plan.resources[0].effect == "remove" and plan.resources[0].identity.endswith(digest)
    assert plugin.mutate("prune", docker.Settings(), data, plan).outcome == Outcome.CHANGED
    assert asyncio.run(plugin.verify(plan, docker.Settings(), data)).outcome == Outcome.COMPLETE


def test_rootless_daemon_merge_preserves_other_settings(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    config = tmp_path / "cfg/docker/daemon.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"dns":["192.0.2.5"],"log-opts":{"compress":"true"}}')
    selection = docker.configuration.Daemon(log_size_mib=7)
    plugin = docker.Plugin()
    assert (
        plugin.prepare("configure", docker.Settings(mode="rootful"), selection).outcome
        == Outcome.BLOCKED
    )
    plan = plugin.prepare("configure", docker.Settings(), selection)
    assert plan.resources[0].identity == str(config)
    assert plugin.mutate("configure", docker.Settings(), selection, plan).outcome == Outcome.CHANGED
    data = json.loads(config.read_bytes())
    assert data["dns"] == ["192.0.2.5"] and data["log-opts"] == {
        "compress": "true",
        "max-size": "7m",
        "max-file": "3",
    }
    assert (
        asyncio.run(plugin.verify(plan, docker.Settings(), selection)).outcome == Outcome.COMPLETE
    )
