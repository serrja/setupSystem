"""Exercise account consent without authenticating or changing any account."""

import asyncio
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from setup_plugin_administration import Login, Plugin, grant_plan, native
from setup_plugin_administration.models import AdminAccount

from setup_system_sdk import Mutation, Outcome, PluginSettings
from setup_system_sdk.native_play import execute


def test_stale_account_cannot_reach_mutation(monkeypatch):
    account = AdminAccount(
        username="fixture",
        uid=1099,
        gid=1099,
        home="/home/fixture",
        shell="/bin/bash",
        sudo_gid=27,
        member=False,
    )
    monkeypatch.setattr(native, "current_boot_identity", lambda: "boot")
    monkeypatch.setattr(native, "observe", lambda name: account)
    login = Login(username="fixture")
    plan = grant_plan(login)
    account = account.validated(uid=1098)
    request = Mutation(
        action_id="grant-sudo", settings={}, inputs={"username": "fixture"}, plan=plan
    )
    result = asyncio.run(
        execute(Plugin(), lambda *args: pytest.fail("stale mutation"), "grant-sudo", request)
    )
    assert result.outcome == Outcome.BLOCKED


@pytest.mark.parametrize("name", ["root", "", "-alice", "etc/other", "alice$(shell whoami)"])
def test_ordinary_name(name):
    with pytest.raises(ValidationError):
        Login(username=name)


def test_public_module_is_an_append_and_rechecks_existing_login():
    text = Path(native.__file__).with_name("membership.yml").read_text()
    preflight, append = yaml.safe_load(text)[0]["tasks"]
    assert "check-existing" in preflight["ansible.builtin.command"]["argv"]
    options = append["ansible.builtin.user"]
    assert options["append"] and not options["create_home"] and not options["move_home"]
    assert options["groups"] == ["sudo"]
    assert set(options).isdisjoint({"uid", "password", "shell", "home", "group"})


def test_wrong_post_identity_is_partial(monkeypatch):
    account = AdminAccount(
        username="fixture",
        uid=1099,
        gid=1099,
        home="/home/fixture",
        shell="/bin/bash",
        sudo_gid=27,
        member=False,
    )
    monkeypatch.setattr(native, "current_boot_identity", lambda: "boot")
    monkeypatch.setattr(native, "observe", lambda name: account)
    login = Login(username="fixture")
    plan = grant_plan(login)
    account = account.validated(member=True, shell="/bin/zsh")
    assert asyncio.run(Plugin().verify(plan, PluginSettings(), login)).outcome == Outcome.PARTIAL


def test_existing_group_membership_requires_separate_effective_policy_check(monkeypatch):
    account = AdminAccount(username="alice", uid=1001, gid=1001, home="/home/alice", shell="/bin/sh", sudo_gid=27, member=True)
    monkeypatch.setattr(native,"observe",lambda name: account)
    monkeypatch.setattr(native,"current_boot_identity",lambda: "boot")
    login=Login(username="alice")
    first=grant_plan(login)
    assert first.outcome == Outcome.NO_CHANGES and "Verify effective access" in first.summary
    check=asyncio.run(Plugin().plan("verify-sudo",PluginSettings(),login))
    assert check.outcome == Outcome.READY and check.resources[0].before == check.resources[0].after
    account=account.validated(member=False)
    assert asyncio.run(Plugin().plan("verify-sudo",PluginSettings(),login)).outcome == Outcome.BLOCKED
