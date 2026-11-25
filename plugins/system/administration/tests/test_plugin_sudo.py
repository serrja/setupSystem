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
    account = AdminAccount(
        username="alice",
        uid=1001,
        gid=1001,
        home="/home/alice",
        shell="/bin/sh",
        sudo_gid=27,
        member=True,
    )
    monkeypatch.setattr(native, "observe", lambda name: account)
    monkeypatch.setattr(native, "current_boot_identity", lambda: "boot")
    login = Login(username="alice")
    first = grant_plan(login)
    assert first.outcome == Outcome.NO_CHANGES and "Verify effective access" in first.summary
    check = asyncio.run(Plugin().plan("verify-sudo", PluginSettings(), login))
    assert check.outcome == Outcome.READY and check.resources[0].before == check.resources[0].after
    account = account.validated(member=False)
    assert (
        asyncio.run(Plugin().plan("verify-sudo", PluginSettings(), login)).outcome
        == Outcome.BLOCKED
    )


def test_effective_policy_readback_requires_fresh_protected_evidence(monkeypatch):
    from setup_plugin_administration import apply_native

    from setup_system_sdk import native_play

    account = AdminAccount(
        username="alice",
        uid=1001,
        gid=1001,
        home="/home/alice",
        shell="/bin/sh",
        sudo_gid=27,
        member=True,
    )
    login = Login(username=account.username)
    monkeypatch.setattr(native, "observe", lambda name: account)
    monkeypatch.setattr(native, "current_boot_identity", lambda: "boot")
    plan = grant_plan(login, check=True)
    request = Mutation(action_id="verify-sudo", settings={}, inputs=login.model_dump(), plan=plan)
    observed = asyncio.run(Plugin().verify(plan, PluginSettings(), login))
    assert observed.outcome == Outcome.NEEDS_USER_ACTION
    assert not observed.resources
    calls = []
    monkeypatch.setattr(native_play, "run_native_play", lambda *args: calls.append(args))
    result = asyncio.run(execute(Plugin(), apply_native, "verify-sudo", request))
    assert result.outcome == Outcome.COMPLETE and result.resources[0].verified
    assert calls == [
        (
            "administration",
            "verify.yml",
            {
                "username": "alice",
                "before_digest": plan.resources[0].before,
                "after_digest": plan.resources[0].after,
            },
            "su",
        )
    ]

    def interrupted(*args):
        raise OSError("terminal closed before the policy readback")

    monkeypatch.setattr(native_play, "run_native_play", interrupted)
    result = asyncio.run(execute(Plugin(), apply_native, "verify-sudo", request))
    assert result.outcome == Outcome.UNKNOWN_OUTCOME
    assert not result.resources


def test_membership_can_be_reconciled_without_certifying_effective_policy(monkeypatch):
    import json

    account = AdminAccount(
        username="alice",
        uid=1001,
        gid=1001,
        home="/home/alice",
        shell="/bin/sh",
        sudo_gid=27,
        member=False,
    )
    monkeypatch.setattr(native, "observe", lambda name: account)
    monkeypatch.setattr(native, "current_boot_identity", lambda: "boot")
    login = Login(username="alice")
    plan = grant_plan(login)
    assert plan.outcome == Outcome.READY
    account = account.validated(member=True)
    verified = asyncio.run(Plugin().verify(plan, PluginSettings(), login))
    assert verified.outcome == Outcome.CHANGED
    assert [r.kind for r in verified.resources] == ["group-membership"]
    assert "Verify effective access" in verified.message
    assert not any(r.kind == "policy-check" for r in verified.resources)
    manifest = json.loads(Path(native.__file__).with_name("manifest.json").read_text())
    assert {a["id"] for a in manifest["actions"] if a.get("read_only")} == {"verify-sudo"}


@pytest.mark.parametrize("observation", ["matched", "uid-changed", "already-member"])
def test_sudo_grant_validates_the_same_account_on_both_sides(monkeypatch, observation):
    import subprocess
    from types import SimpleNamespace

    account = AdminAccount(
        username="fixture",
        uid=1042,
        gid=1042,
        home="/home/fixture",
        shell="/bin/sh",
        sudo_gid=27,
        member=False,
    )
    monkeypatch.setattr(native, "os", SimpleNamespace(geteuid=lambda: 0))
    monkeypatch.setattr(native, "current_boot_identity", lambda: "synthetic")
    monkeypatch.setattr(
        native, "observe", lambda name: account if name == account.username else None
    )
    before = native.fingerprint(account)
    desired = account.validated(member=True)
    after = native.fingerprint(desired)
    checked = []
    monkeypatch.setattr(
        native, "require_installed_resource", lambda source, expected: checked.append(expected)
    )
    monkeypatch.setattr(native, "verify_policy", lambda: checked.append("policy"))
    monkeypatch.setattr(native, "verify_effective_access", lambda actual: checked.append(actual))
    calls = []

    def append_fixture(argv, *, stdin, stdout, stderr, env, check):
        nonlocal account
        assert argv[0] == "/usr/bin/systemd-run"
        assert (
            "--property=InaccessiblePaths=/usr/sbin/useradd -/usr/sbin/adduser -/usr/sbin/luseradd"
            in argv
        )
        assert argv[-4:] == ["-e", "selected_user=fixture", "-e", "before_digest=" + before]
        assert all(s == subprocess.DEVNULL for s in (stdin, stdout, stderr))
        assert check and env.keys() == {"PATH", "HOME", "LC_ALL"}
        account = desired if observation == "matched" else desired.validated(uid=1055)
        calls.append(argv)

    monkeypatch.setattr(native.subprocess, "run", append_fixture)
    if observation == "already-member":
        account = desired
        with pytest.raises(ValueError, match="confirmed grant changed"):
            native.append(account.username, before, after)
        assert calls == []
    elif observation == "uid-changed":
        with pytest.raises(ValueError, match="identity"):
            native.append(account.username, before, after)
        assert checked[-1] == "policy"
    else:
        native.append(account.username, before, after)
        assert len(calls) == 1 and checked[-2:] == ["policy", desired]
    assert checked[0] == "plugins/administration/setup_plugin_administration/grant.py"


def test_native_policy_query_never_appends_after_account_change(monkeypatch):
    import io
    from types import SimpleNamespace

    from setup_plugin_administration.models import GrantConfirmation

    account = AdminAccount(
        username="fixture",
        uid=1005,
        gid=1005,
        home="/home/fixture",
        shell="/bin/sh",
        sudo_gid=27,
        member=True,
    )
    monkeypatch.setattr(native, "current_boot_identity", lambda: "synthetic")
    monkeypatch.setattr(native, "observe", lambda name: account)
    digest = native.fingerprint(account)
    data = (
        GrantConfirmation(username="fixture", before_digest=digest, after_digest=digest)
        .model_dump_json()
        .encode()
    )
    logs = []
    monkeypatch.setattr(native, "verify_effective_access", lambda a: logs.append(a.uid))
    monkeypatch.setattr(native, "verify_policy", lambda: logs.append("policy"))
    monkeypatch.setattr(
        native, "append", lambda *args: pytest.fail("read-only action attempted mutation")
    )

    def invoke(raw):
        monkeypatch.setattr(
            native,
            "sys",
            SimpleNamespace(
                argv=["grant.py", "verify-sudo"], stdin=SimpleNamespace(buffer=io.BytesIO(raw))
            ),
        )
        native.main()

    invoke(data)
    assert logs == ["policy", 1005]
    account = account.validated(gid=1020)
    with pytest.raises(ValueError, match="identity changed"):
        invoke(data)
    with pytest.raises(ValueError, match="size"):
        invoke(data + b" " * native.ADMIN_REQUEST_BYTES)
    assert logs == ["policy", 1005]
