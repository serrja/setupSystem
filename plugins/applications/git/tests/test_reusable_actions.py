"""Exercise account logout, install-independent style updates and the thin UI panel."""

import asyncio
import io
import json
import runpy
import sys

import pytest
from setup_plugin_git import Account, Plugin, Settings, Style
from setup_plugin_git.ui import GitPanel
from textual.app import App, ComposeResult

from setup_system_sdk import Outcome
from setup_system_sdk.ui import UIContext


def test_local_logout_account_and_styles(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    accounts = {"a", "b"}
    import setup_plugin_git as git

    def cli(*argv, **kw):
        if "logout" in argv:
            accounts.remove(argv[-1])
            return 0, ""
        return 0, "\n".join(accounts)

    monkeypatch.setattr(git, "command", cli)
    monkeypatch.setattr(git.accounts, "local_names", lambda _: sorted(accounts))
    monkeypatch.setattr(git.accounts, "observed_names", lambda _: sorted(accounts))
    monkeypatch.setattr(git.accounts, "authenticated", lambda _: True)
    monkeypatch.setattr(git.accounts, "online", lambda *_: True)
    monkeypatch.setattr(git.accounts, "selector_supported", lambda: True)
    settings = Settings()
    plugin = Plugin()
    data = Account(account="b")
    plan = plugin.prepare("logout", settings, data)
    assert plan.outcome == Outcome.READY
    assert plugin.mutate("logout", settings, data, plan).outcome == Outcome.CHANGED
    assert asyncio.run(plugin.verify(plan, settings, data)).outcome == Outcome.COMPLETE
    assert accounts == {"a"}
    cfg = tmp_path / ".gitconfig"
    cfg.write_text("[alias]\n\tmine=status\n")
    style = plugin.prepare("configure-style", settings, Style())
    assert plugin.mutate("configure-style", settings, Style(), style).outcome == Outcome.CHANGED
    assert asyncio.run(plugin.verify(style, settings, Style())).outcome == Outcome.COMPLETE
    assert "mine=status" in cfg.read_text()
    assert plugin.prepare("configure-style", settings, Style()).outcome == Outcome.NO_CHANGES


def test_thin_ui_routes_stable_action_ids():
    chosen = []

    class Host(App):
        def compose(self) -> ComposeResult:
            yield GitPanel(UIContext("git", chosen.append))

    async def run():
        async with Host().run_test(size=(110, 20)) as pilot:
            await pilot.click("#upload-key")
            await pilot.pause()

    asyncio.run(run())
    assert chosen == ["upload-key"]


@pytest.mark.parametrize("plugin", ["git", "codex", "happ", "docker", "neovim", "obsidian"])
def test_user_make_recipe_returns_safe_blocked_wire(monkeypatch, capsys, plugin):
    # Native mutation guards consume the real recipe input and suppress invalid request data.
    secret = "synthetic-rejected-secret"
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(secret.encode())))
    monkeypatch.setattr(sys, "argv", ["recipe", "invalid"])
    runpy.run_module("setup_plugin_" + plugin + ".action", run_name="__main__")
    wire = capsys.readouterr().out
    assert json.loads(wire)["outcome"] == "blocked" and secret not in wire
