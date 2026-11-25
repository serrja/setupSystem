"""Account authentication is independent from installation and user-interface discovery."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import setup_plugin_codex as codex
import setup_plugin_docker as docker
import setup_plugin_gh as gh
import setup_plugin_git as git
from setup_plugin_gh import accounts

from setup_system_sdk import Inputs, LifecycleAction, Outcome, Plan, apt, user_terminal


def test_inspect_and_install_never_start_authentication(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("An unselected account action ran during inspection or package preview")

    monkeypatch.setattr(user_terminal, "run_native", unexpected)
    monkeypatch.setattr(gh, "observed_login", unexpected)
    monkeypatch.setattr(gh, "command", unexpected)
    monkeypatch.setattr(accounts, "command", unexpected)
    monkeypatch.setattr(codex, "command", unexpected)
    monkeypatch.setattr(docker.registry, "store", unexpected)
    seen = []
    expected = Plan(summary="Package preview")

    def preview(folder: Path):
        seen.append(json.loads((folder / "apt.json").read_bytes())["names"])
        return expected

    monkeypatch.setattr(apt, "package_plan", preview)
    for backend in (git.Plugin(), gh.Plugin(), docker.Plugin(), codex.Plugin()):
        settings = backend.settings_model()
        assert asyncio.run(backend.inspect(settings)).outcome == Outcome.NO_CHANGES
        if isinstance(backend, codex.Plugin):
            monkeypatch.setattr(codex.release, "resolve", lambda: "fixture")
            monkeypatch.setattr(codex.release, "plan", lambda *_: expected)
        assert asyncio.run(backend.plan(LifecycleAction.INSTALL, settings, Inputs())) == expected
    assert "gh" not in seen[0] and "gh" in seen[1]
    assert not ({"login", "status", "logout"} & git.Plugin.inputs.keys())
    assert {"login", "status", "logout"} <= gh.Plugin.inputs.keys()


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (200, b'{"login":"fixture"}', True),
        (200, b'{"login":"other"}', False),
        (307, b"", False),
        (200, b"x" * 65537, False),
    ],
)
def test_inactive_account_reads_only_a_bounded_same_host_response(
    monkeypatch, status, body, expected
):
    selected = accounts.Selection(host="git.example.test", account="fixture")
    seen = []

    def native(*argv, capture=False):
        assert argv == (
            accounts.GH,
            "auth",
            "token",
            "--hostname",
            selected.host,
            "--user",
            "fixture",
        )
        assert capture
        return 0, "synthetic-placeholder"

    def endpoint(request):
        assert str(request.url) == "https://git.example.test/api/v3/user"
        seen.append(request.url.host)
        return httpx.Response(status, headers={"Location": "https://unrelated.test/"}, content=body)

    client = httpx.Client
    monkeypatch.setattr(accounts, "command", native)
    monkeypatch.setattr(
        accounts.httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(endpoint), **kw)
    )
    assert accounts.named_account(selected) is expected and seen == [selected.host]
