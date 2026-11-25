"""Confirm sign-in through the host-scoped native CLI and its public user API."""

import asyncio

import pytest
import setup_plugin_gh as app

from setup_system_sdk import Mutation, Outcome


@pytest.mark.parametrize(
    ("terminal_rc", "account", "expected"),
    [
        (0, "fixture", Outcome.CHANGED),
        (0, "other", Outcome.NEEDS_USER_ACTION),
        (1, "fixture", Outcome.NEEDS_USER_ACTION),
    ],
)
def test_browser_sign_in_checks_the_selected_account(
    tmp_path, monkeypatch, terminal_rc, account, expected
):
    gh = tmp_path / "gh"
    gh.touch()
    monkeypatch.setattr(app, "GH", gh)
    monkeypatch.setattr(app.user_terminal, "available", lambda: True)
    monkeypatch.setattr(app, "unlocked_store", lambda: True)
    selected = app.SignIn(account="fixture", host="git.example.test")
    settings = app.Settings(enterprise_host=selected.host)
    active = []
    checks = []

    def cli(*args, capture=False):
        assert capture
        if args[1:] == ("auth", "status", "--help"):
            return 0, " --json fields --no-refresh "
        assert args[:10] == (
            str(gh),
            "auth",
            "status",
            "--active",
            "--hostname",
            selected.host,
            "--no-refresh",
            "--json",
            "hosts",
            "--jq",
        )
        assert '.tokenSource == "keyring"' in args[10] and ".login" in args[10]
        checks.append(True)
        return (0, active[-1] + "\ttrue") if active else (1, "")

    def terminal(argv):
        assert argv == [
            str(gh),
            "auth",
            "login",
            "--web",
            "--skip-ssh-key",
            "--hostname",
            selected.host,
        ]
        active.append(account)
        return terminal_rc

    monkeypatch.setattr(app, "command", cli)
    monkeypatch.setattr(app.user_terminal, "run_native", terminal)
    plugin = app.Plugin()
    plan = plugin.prepare("login", settings, selected)
    assert plan.outcome == Outcome.READY
    result = asyncio.run(
        plugin.apply_request(
            "login",
            Mutation(
                action_id="login",
                inputs=selected.model_dump(),
                settings=settings.model_dump(),
                plan=plan,
            ),
        )
    )
    assert result.outcome == expected
    assert len(checks) == 3
    readback = asyncio.run(plugin.verify(plan, settings, selected))
    assert readback.outcome == (
        Outcome.COMPLETE if account == "fixture" else Outcome.NEEDS_USER_ACTION
    )
    if account == "fixture":
        assert plugin.prepare("login", settings, selected).outcome == Outcome.NO_CHANGES


def test_git_login_requires_unlocked_native_store(monkeypatch):
    def locked(*args, capture=False):
        assert args[0] == "/usr/bin/gdbus" and args[-1] == "Locked" and capture
        return 0, "(<true>,)"

    monkeypatch.setattr(app, "command", locked)
    assert not app.unlocked_store()
    monkeypatch.setattr(app, "command", lambda *args, **kw: (0, "(<false>,)"))
    assert app.unlocked_store()


def test_stable_cli_confirms_account_and_requests_storage_review(tmp_path, monkeypatch):
    gh = tmp_path / "gh"
    gh.touch()
    monkeypatch.setattr(app, "GH", gh)
    selected = app.SignIn(account="fixture")

    def cli(*args, capture=False):
        assert capture
        if args[1] == "api":
            return 0, "fixture"
        assert args[1:] == ("auth", "status", "--help")
        return 0, "--hostname string"

    monkeypatch.setattr(app, "command", cli)
    plugin = app.Plugin()
    plan = plugin.prepare("login", app.Settings(), selected)
    assert plan.outcome == Outcome.NEEDS_USER_ACTION and "gh auth status" in plan.summary
    assert (
        asyncio.run(plugin.verify(plan, app.Settings(), selected)).outcome
        == Outcome.NEEDS_USER_ACTION
    )


def test_account_host_is_approved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "observed_login", lambda _: "different")
    monkeypatch.setattr(app, "unlocked_store", lambda: True)
    monkeypatch.setattr(app.user_terminal, "available", lambda: False)
    plan = app.Plugin().prepare(
        "login", app.Settings(), app.SignIn(account="fixture", host="github.com.evil.test")
    )
    assert plan.outcome == Outcome.BLOCKED
    approved = app.Settings(enterprise_host="code.example.org")
    plan = app.Plugin().prepare(
        "login", approved, app.SignIn(account="fixture", host="code.example.org")
    )
    assert plan.outcome in (Outcome.NEEDS_USER_ACTION, Outcome.BLOCKED)
