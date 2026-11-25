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
