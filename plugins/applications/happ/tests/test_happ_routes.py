import asyncio
import base64
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from setup_plugin_happ import Plugin, PublicRoute, Route, Subscription, SubscriptionID, root

from setup_system_sdk import Mutation, Outcome, PluginSettings


def apply(plugin: Plugin, action: str, selection: Subscription | Route) -> None:
    plan = asyncio.run(plugin.plan(action, PluginSettings(), selection))
    assert plan.outcome == Outcome.READY
    result = asyncio.run(
        plugin.apply_request(
            action,
            Mutation(
                action_id=action, settings={}, inputs=selection.model_dump(mode="json"), plan=plan
            ),
        )
    )
    assert result.outcome == Outcome.CHANGED


def test_subscription_scoped_routing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    async def synthetic_secret(*args):
        return SecretStr("https://example.test/fixture")

    monkeypatch.setattr("setup_plugin_happ.read_secret", synthetic_secret)
    plugin = Plugin()
    for key, form in (("work", "standard"), ("personal", "provider-json")):
        apply(
            plugin,
            "save-subscription",
            Subscription(subscription=key, title=key, url_reference=uuid4(), source_type=form),
        )
    rule = Route(
        subscription="work", domain="example.test", network="192.0.2.0/24", disposition="block"
    )
    apply(plugin, "export-route", rule)
    href = (root() / "work" / "user-route.happ").read_text().strip()
    rendered = PublicRoute.model_validate_json(
        base64.b64decode(href.removeprefix("happ://routing/add/"), validate=True)
    )
    assert rendered.block_sites == ["full:example.test"] and rendered.block_ip == ["192.0.2.0/24"]
    assert (
        asyncio.run(plugin.plan("export-route", PluginSettings(), rule)).outcome
        == Outcome.NO_CHANGES
    )
    assert (
        asyncio.run(
            plugin.plan(
                "export-route",
                PluginSettings(),
                rule.model_copy(update={"subscription": "personal"}),
            )
        ).outcome
        == Outcome.BLOCKED
    )
    assert not (root() / "personal" / "user-route.happ").exists()
    assert (
        asyncio.run(
            plugin.plan("reconnect", PluginSettings(), SubscriptionID(subscription="work"))
        ).outcome
        == Outcome.NEEDS_USER_ACTION
    )
    assert asyncio.run(plugin.choices("subscriptions", "w", PluginSettings())) == ["work"]


def test_raw_subscription_url_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        Subscription(
            subscription="work", title="Work", url_reference="https://example.test/private-token"
        )
    assert "private-token" not in str(exc.value)


def test_dns_and_order_stay_in_one_subscription(tmp_path, monkeypatch):
    import setup_plugin_happ as happ

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    p = happ.subscription_file("example")
    p.parent.mkdir(parents=True)
    p.write_text(
        Subscription(
            subscription="example", title="Example", url_reference=uuid4()
        ).model_dump_json()
    )
    a = Route(subscription="example", domain="one.example", domestic_dns="192.0.2.1")
    path, raw = happ.route_export(a)
    path.write_bytes(raw)
    _, updated = happ.route_export(
        Route(subscription="example", domain="two.example", position="first")
    )
    wire = PublicRoute.model_validate_json(
        base64.b64decode(updated.removeprefix(b"happ://routing/add/"))
    )
    assert str(wire.domestic_ip) == "192.0.2.1" and wire.domestic_type == "DoU"
    assert wire.direct_sites == ["full:two.example", "full:one.example"]


def test_local_subscription_cleanup_preserves_other_records(tmp_path, monkeypatch):
    import setup_plugin_happ as app

    from setup_system_sdk import ownership_context

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    async def secret(*a):
        return SecretStr("https://example.invalid/fixture-secret")

    monkeypatch.setattr(app, "read_secret", secret)
    plugin = Plugin()
    settings = PluginSettings()
    records = []

    def apply_checked(action, data):
        with ownership_context(tuple(records)):
            plan = asyncio.run(plugin.plan(action, settings, data))
            assert plan.outcome == Outcome.READY
            result = asyncio.run(
                plugin.apply_request(
                    action,
                    Mutation(
                        action_id=action,
                        plan=plan,
                        settings={},
                        inputs=data.model_dump(mode="json"),
                        holdings=tuple(records),
                    ),
                )
            )
            assert result.outcome == Outcome.CHANGED
            return asyncio.run(plugin.verify(plan, settings, data))

    for name in ("one", "two"):
        record = Subscription(subscription=name, title="Example", url_reference=uuid4())
        records.extend(apply_checked("save-subscription", record).resources)
    fields = Route(subscription="one", domain="example.test")
    checked = apply_checked("export-route", fields)
    assert checked.outcome == Outcome.NEEDS_USER_ACTION and checked.resources[0].verified
    records.extend(checked.resources)
    assert asyncio.run(plugin.choices("subscriptions", "t", settings)) == ["two"]
    source = SubscriptionID(subscription="one")
    for action in ("import", "update-subscription", "remove-subscription", "reconnect"):
        assert (
            asyncio.run(plugin.plan(action, settings, source)).outcome == Outcome.NEEDS_USER_ACTION
        )
    removal = apply_checked("forget-local", app.ForgetSubscription(subscription="one"))
    assert removal.outcome == Outcome.COMPLETE
    assert not app.subscription_file("one").exists() and app.subscription_file("two").is_file()
    assert not (app.root() / "one/user-route.happ").exists()


def test_native_only_url_handling_avoids_keyring_and_keeps_local_routes(tmp_path, monkeypatch):
    import setup_plugin_happ as app

    monkeypatch.setenv("HOME", str(tmp_path))
    for scope in ("CONFIG", "DATA", "STATE", "CACHE"):
        monkeypatch.setenv("XDG_" + scope + "_HOME", str(tmp_path / scope.lower()))

    async def rejected(*args):
        pytest.fail("The native-only URL mode must never access keyring")

    monkeypatch.setattr(app, "read_secret", rejected)
    plugin = Plugin()
    form = Subscription(subscription="office", title="Office", url_handling="enter-in-happ")
    apply(plugin, "save-subscription", form)
    content = app.subscription_file("office").read_text()
    assert "url_reference" not in content and "https:" not in content
    for action in ("import", "update-subscription"):
        result = asyncio.run(
            plugin.plan(action, PluginSettings(), SubscriptionID(subscription="office"))
        )
        assert result.outcome == Outcome.NEEDS_USER_ACTION and "directly in" in result.summary
    apply(plugin, "export-route", Route(subscription="office", domain="example.test"))
    assert (app.root() / "office/user-route.happ").is_file()
    with pytest.raises(ValidationError):
        Subscription(subscription="office", title="Office", url_handling="system-keyring")
    with pytest.raises(ValidationError):
        Subscription(
            subscription="office",
            title="Office",
            url_handling="enter-in-happ",
            url_reference=uuid4(),
        )
    # The previous on-disk format, containing a UUID without an explicit mode, remains readable.
    reference = uuid4()
    old = Subscription.model_validate(
        {"subscription": "old", "title": "Old", "url_reference": str(reference)}
    )
    assert old.url_handling == "system-keyring" and old.url_reference == reference
