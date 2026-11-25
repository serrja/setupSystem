"""Check GitHub CLI account selection on Debian stable and JSON-capable clients."""

import asyncio

import httpx
import pytest
import setup_plugin_gh as app
import yaml
from setup_plugin_gh import accounts

from setup_system_sdk import Mutation, Outcome


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    folder = tmp_path / ".config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(folder))
    path = folder / "gh/hosts.yml"
    path.parent.mkdir(parents=True, mode=0o700)
    return path


def write_config(path, names):
    content = (
        {
            "github.com": {
                "user": names[0],
                "users": {name: {"oauth_token": "synthetic-private-value"} for name in names},
            }
        }
        if names
        else {}
    )
    path.write_text(yaml.safe_dump(content))
    path.chmod(0o600)


@pytest.mark.parametrize("modern", [False, True], ids=["gh-2.46", "gh-2.98"])
@pytest.mark.parametrize("selected", ["Active", "Inactive"])
def test_verified_scoped_logout_and_offline_readback(config, monkeypatch, modern, selected):
    names = ["Active", "Inactive"]
    write_config(config, names)
    online = [True]
    calls = []
    scoped_checks = []

    def cli(*argv, capture=False):
        tail = argv[1:]
        if tail == ("auth", "status", "--help"):
            return 0, "--hostname --json --no-refresh" if modern else "--hostname"
        if tail == ("auth", "logout", "--help"):
            return 0, "--hostname string --user string"
        if tail[:2] == ("auth", "status"):
            assert modern and "--no-refresh" in tail and "--json" in tail
            return 0, "\n".join(names)
        if tail[0] == "api":
            assert tail == ("api", "--hostname", "github.com", "user", "--jq", ".login")
            return (0, names[0]) if online[0] and names else (1, "")
        assert tail == ("auth", "logout", "--hostname", "github.com", "--user", selected)
        calls.append(tail)
        names.remove(selected)
        write_config(config, names)
        return 0, ""

    def account_api(data):
        scoped_checks.append(data.account)
        if not online[0] or data.account not in names:
            raise ValueError("Account unavailable")
        return True

    monkeypatch.setattr(app, "command", cli)
    monkeypatch.setattr(accounts, "command", cli)
    monkeypatch.setattr(accounts, "named_account", account_api)
    plugin = app.Plugin()
    settings = app.Settings(enterprise_host="enterprise.test")
    data = app.Account(account=selected)
    assert asyncio.run(plugin.choices("accounts", "", settings)) == ["Active", "Inactive"]
    assert asyncio.run(plugin.choices("hosts", "enter", settings)) == ["enterprise.test"]
    assert plugin.prepare("status", settings, data).outcome == Outcome.NO_CHANGES
    assert scoped_checks == (["Inactive"] if selected == "Inactive" else [])
    online[0] = False
    assert plugin.prepare("status", settings, data).outcome == Outcome.NEEDS_USER_ACTION
    assert plugin.prepare("logout", settings, data).outcome == Outcome.NEEDS_USER_ACTION
    online[0] = True
    plan = plugin.prepare("logout", settings, data)
    assert plan.outcome == Outcome.READY
    mutation = Mutation(
        action_id="logout", inputs=data.model_dump(), settings=settings.model_dump(), plan=plan
    )
    assert asyncio.run(plugin.apply_request("logout", mutation)).outcome == Outcome.CHANGED
    assert len(calls) == 1 and names == (["Active"] if selected == "Inactive" else ["Inactive"])
    online[0] = False
    assert asyncio.run(plugin.verify(plan, settings, data)).outcome == Outcome.NEEDS_USER_ACTION
    online[0] = True
    assert asyncio.run(plugin.verify(plan, settings, data)).outcome == Outcome.COMPLETE
    assert plugin.prepare("logout", settings, data).outcome == Outcome.NO_CHANGES


def test_last_account_requires_independent_reachability(config, monkeypatch):
    write_config(config, [])
    monkeypatch.setattr(accounts, "command", lambda *a, **kw: (0, "--hostname"))
    client = httpx.Client
    code = [503]

    def endpoint(request):
        assert str(request.url) == "https://api.github.com/rate_limit"
        assert "authorization" not in request.headers
        return httpx.Response(code[0], headers={"X-GitHub-Request-Id": "fixture"})

    monkeypatch.setattr(
        accounts.httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(endpoint), **kw)
    )
    data = app.Account(account="last")
    assert accounts.verify_logout(data).outcome == Outcome.NEEDS_USER_ACTION
    code[0] = 200
    assert accounts.verify_logout(data).outcome == Outcome.COMPLETE


@pytest.mark.parametrize(
    "bad",
    [
        "github.com: {user: one, user: two}",
        "github.com: &alias {user: someone}\nother.test: *alias",
        "github.com: {user: one, users: {One: {}, one: {}}}",
        "github.com: {users: {someone: {oauth_token: secret-fixture, oauth_token: second}}}",
        "github.com: {user: [secret-fixture}",
        "github.com: {user: someone, users: {other: {}}}",
    ],
)
def test_private_inventory_rejects_ambiguity_without_source_in_errors(config, bad):
    config.write_text(bad)
    config.chmod(0o600)
    with pytest.raises(ValueError, match="Cannot safely") as exc:
        accounts.local_names("github.com")
    assert "secret-fixture" not in str(exc.value)


def test_no_unscoped_fallback_or_unprotected_config(config, monkeypatch):
    write_config(config, ["one", "two"])
    data = app.Account(account="one")
    monkeypatch.setattr(accounts, "command", lambda *a, **kw: (0, "--hostname"))
    plan = accounts.prepare(data, logout=True)
    assert plan.outcome == Outcome.NEEDS_USER_ACTION and "--user" in plan.summary
    config.chmod(0o644)
    with pytest.raises(ValueError, match="Cannot safely"):
        accounts.local_names("github.com")
    real = config.with_suffix(".private")
    config.rename(real)
    config.symlink_to(real)
    with pytest.raises(ValueError, match="Cannot safely"):
        accounts.local_names("github.com")
