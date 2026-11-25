"""Retired package names permit cleanup of earlier Git-owned installations only."""

import asyncio
import io
import sys
from uuid import uuid4

import pytest
from pydantic import ValidationError
from setup_plugin_git import Plugin, Settings

from setup_system.resource_ledger import ResourceLedger
from setup_system_sdk import Outcome, Plan, Resource, Result, apt, ownership_context


def row(name, **update):
    return Resource(
        kind="package", identity=name, ownership="created", after="1", cleanup=True
    ).model_copy(update=update)


def test_retired_names_are_removal_only_and_require_the_existing_receipt(monkeypatch):
    policy = apt.Packages.model_validate_json((Plugin.package_folder / "apt.json").read_bytes())
    assert "gh" not in policy.candidates() and policy.accepts_removal("gh")
    for retired in [("gh", "gh"), ("git",)]:
        with pytest.raises(ValidationError):
            apt.Packages(names=("git",), retired_names=retired)

    monkeypatch.setattr(apt, "current_target", lambda: ("amd64", True))
    monkeypatch.setattr(apt, "installed", lambda _: "1")

    def native(argv, *_args, **_kwargs):
        if argv[0].endswith("dpkg-query"):
            return "install|||amd64"
        assert "--simulate" in argv and "remove" in argv and argv[-1] == "gh"
        return "Remv gh [1]"

    monkeypatch.setattr(apt, "query", native)
    with ownership_context((row("gh", ownership="preexisting", cleanup=False), row("unrelated"))):
        assert apt.uninstall_plan(Plugin.package_folder).outcome == Outcome.NO_CHANGES
    with ownership_context(
        (row("gh"), row("libglib2.0-bin", ownership="shared", cleanup=False), row("unrelated"))
    ):
        removal = apt.uninstall_plan(Plugin.package_folder)
        assert [(r.identity, r.before, r.effect) for r in removal.resources] == [
            ("gh", "1", "remove")
        ]
    monkeypatch.setattr(apt, "installed", lambda _: "2")
    with ownership_context((row("gh"),)), pytest.raises(ValueError, match="changed"):
        apt.uninstall_plan(Plugin.package_folder)


@pytest.mark.parametrize("action", ["uninstall", apt.PackageAction.REMOVE_RETIRED])
def test_active_github_cli_consumer_protects_git_owned_retired_package(
    tmp_path, monkeypatch, action
):
    monkeypatch.setenv("HOME", str(tmp_path))
    ledger = ResourceLedger(tmp_path / "resources.sqlite3")
    package = row("gh")
    consumer = package.model_copy(
        update={"ownership": "preexisting", "cleanup": False, "before": "1"}
    )

    async def record(plugin, resource):
        op = uuid4()
        plan = Plan(summary="Synthetic installation", resources=[resource])
        await ledger.prepare(op, plugin, "install", "release", plan)
        await ledger.finish(
            op, Result(outcome=Outcome.COMPLETE, message="Fixture", resources=[resource])
        )

    async def check():
        await record("git", package)
        await record("gh", consumer)
        removal = package.model_copy(update={"before": "1", "after": None, "effect": "remove"})
        with pytest.raises(ValueError, match="Another plugin needs"):
            await ledger.prepare(
                uuid4(),
                "git",
                action,
                "release",
                Plan(summary="Remove retired gh", resources=[removal]),
            )
        assert (await ledger.holdings("git"))[0].ownership == "created"
        assert (await ledger.holdings("gh"))[0].ownership == "preexisting"

    asyncio.run(check())


def test_retirement_checks_only_one_selected_owned_package_at_each_boundary(tmp_path, monkeypatch):
    plugin = Plugin()
    settings = Settings()
    action = apt.PackageAction.REMOVE_RETIRED
    inputs = apt.RetiredPackage(name="gh")
    assert asyncio.run(plugin.choices("retired-packages", "gh", settings)) == ["gh"]
    packages = apt.Packages.model_validate_json((plugin.package_folder / "apt.json").read_bytes())
    versions = dict.fromkeys(("gh", "git", "openssh-client", "libglib2.0-bin"), "1")
    monkeypatch.setattr(apt, "current_target", lambda: ("amd64", True))
    monkeypatch.setattr(apt, "installed", versions.get)

    def native(argv, *_args, **_kwargs):
        if argv[0].endswith("dpkg-query"):
            assert argv[-1] == "gh"
            return "install|||amd64"
        assert argv == ["/usr/bin/apt-get", "--simulate", "remove", "--", "gh"]
        return "Remv gh [1]"

    monkeypatch.setattr(apt, "query", native)
    with ownership_context(tuple(row(name) for name in versions)):
        plan = asyncio.run(plugin.plan(action, settings, inputs))
        with pytest.raises(ValueError, match="protected retired"):
            plugin.prepare(action, settings, apt.RetiredPackage(name="git"))
    assert plan.outcome == Outcome.READY
    assert [(r.identity, r.before, r.effect) for r in plan.resources] == [("gh", "1", "remove")]
    approval = apt.Approval.model_validate(
        {"plugin_id": "git", "action_id": action.value, "plan": plan}
    )
    assert approval.action_id is action
    packages.check_retired_selection(plan.resources)
    for forged in [[], [*plan.resources, row("git")], [row("git", effect="remove")], [row("gh")]]:
        with pytest.raises(ValueError, match="exactly one reviewed retired"):
            packages.check_retired_selection(forged)

    header = "VERSION 3\nAPT::Architecture=amd64\n\n"
    removal = "gh 1 amd64 none > - - none **REMOVE**\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(header + removal))
    apt.verify_hook(tmp_path, approval)
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(header + removal + removal.replace("gh ", "git "))
    )
    with pytest.raises(ValueError, match="Unapproved dpkg removal"):
        apt.verify_hook(tmp_path, approval)
    assert asyncio.run(plugin.verify(plan, settings, inputs)).outcome == Outcome.PARTIAL
    versions.pop("gh")
    assert asyncio.run(plugin.verify(plan, settings, inputs)).outcome == Outcome.COMPLETE
    assert set(versions) == {"git", "openssh-client", "libglib2.0-bin"}

    with ownership_context((row("gh", ownership="preexisting", cleanup=False),)):
        assert asyncio.run(plugin.plan(action, settings, inputs)).outcome == Outcome.NO_CHANGES
