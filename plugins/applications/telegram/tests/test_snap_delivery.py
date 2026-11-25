"""Exercise Telegram's reviewed snap changes with local Store and daemon fixtures."""

import base64

import httpx
import pytest
from setup_plugin_telegram import snaps

from setup_system_sdk import Outcome, Resource, ownership_context
from setup_system_sdk.native import LauncherProfile, launcher_file


def release(name, rev=11):
    return snaps.Revision(
        name=name,
        revision=rev,
        version=f"1.{rev}",
        sha3=f"{rev:02x}" * 48,
        snap_id=snaps.IDENTITIES.get(name, ("R" * 32, ""))[0],
    )


def native(row):
    return snaps.Native(
        name=row.name,
        id=row.snap_id,
        revision=str(row.revision),
        version=row.version,
        status="active",
        confinement="strict",
    )


@pytest.fixture
def daemon(monkeypatch):
    db = {}
    store = {name: release(name) for name in (snaps.SYSTEM_SNAP, snaps.NAME, "runtime")}
    specs = {
        snaps.SYSTEM_SNAP: snaps.StoreSpec.model_validate({"type": "snapd"}),
        "runtime": snaps.StoreSpec.model_validate(
            {"type": "base", "slots": {"icons": {"interface": "content"}}}
        ),
        snaps.NAME: snaps.StoreSpec.model_validate(
            {
                "base": "none",
                "plugs": {"icons": {"interface": "content", "default-provider": "runtime"}},
            }
        ),
    }
    monkeypatch.setattr(snaps, "local_content", lambda name: specs[name].content())
    calls = []
    pending = []
    monkeypatch.setattr(snaps, "current_target", lambda: ("amd64", True))
    monkeypatch.setattr(snaps, "installed", lambda name: None)
    monkeypatch.setattr(snaps, "local", lambda: {name: native(row) for name, row in db.items()})
    monkeypatch.setattr(
        snaps,
        "latest",
        lambda name, arch: (store[name], specs[name]),
    )
    monkeypatch.setattr(snaps, "require_installed_resource", lambda source, rel: None)
    monkeypatch.setattr(snaps.os, "geteuid", lambda: 0)

    def cli(*argv, timeout=20):
        calls.append(argv)
        if argv[0] == "known":
            sid = argv[2].split("=", 1)[1]
            row = next(r for r in db.values() if r.snap_id == sid)
            return "snap-sha3-384: " + base64.urlsafe_b64encode(bytes.fromhex(row.sha3)).decode()
        if argv[0] in {"refresh", "install"}:
            assert argv[1:3] == ("--no-wait", "--channel=latest/stable")
            assert argv[3] == f"--revision={store[argv[-1]].revision}"
            pending.append(store[argv[-1]])
            return "159"
        if argv == ("watch", "159"):
            row = pending.pop()
            db[row.name] = row
            return ""
        if argv == ("save", snaps.NAME):
            return "Set Snap Age Version Rev Size Notes\n42 telegram-desktop 0 1 11 40MB -\n"
        if argv == ("check-snapshot", "42", snaps.NAME):
            return ""
        if argv == ("remove", snaps.NAME):
            del db[snaps.NAME]
            return ""
        pytest.fail("Unreviewed native call")

    monkeypatch.setattr(snaps, "cli", cli)
    return db, store, calls


def test_stable_review_install_update_and_guarded_removal(daemon):
    existing, remote, calls = daemon
    plan = snaps.prepare("install")
    assert plan.outcome == Outcome.READY and [r.identity for r in plan.resources] == [
        snaps.SYSTEM_SNAP,
        "runtime",
        snaps.NAME,
    ]
    assert plan.resources[0].ownership == "shared" and not plan.resources[0].cleanup
    snaps.apply(snaps.Approval(action="install", plan=plan))
    verified = snaps.verify(plan)
    assert verified.outcome == Outcome.COMPLETE and all(r.verified for r in verified.resources)
    assert snaps.prepare("install").outcome == Outcome.NO_CHANGES
    assert snaps.prepare("uninstall").outcome == Outcome.NO_CHANGES
    remote[snaps.NAME] = release(snaps.NAME, 12)
    with ownership_context(tuple(verified.resources)):
        update = snaps.prepare("update")
        assert update.outcome == Outcome.READY
        snaps.apply(snaps.Approval(action="update", plan=update))
        assert snaps.prepare("uninstall").outcome == Outcome.BLOCKED
    owned = verified.resources[-1].model_copy(
        update={"after": remote[snaps.NAME].model_dump_json()}
    )
    with ownership_context((owned,)):
        remove = snaps.prepare("uninstall")
        assert remove.resources[0].before == owned.after
        snaps.apply(snaps.Approval(action="uninstall", plan=remove))
    assert list(existing) == [snaps.SYSTEM_SNAP, "runtime"]
    assert snaps.verify(remove).outcome == Outcome.COMPLETE
    assert (
        calls.index(("save", snaps.NAME))
        < calls.index(("check-snapshot", "42", snaps.NAME))
        < calls.index(("remove", snaps.NAME))
    )
    assert all("--purge" not in call for call in calls)


def test_foreign_installations_stale_store_and_snapshot_errors_preserve_data(daemon, monkeypatch):
    existing, remote, calls = daemon
    original = snaps.prepare("install")
    remote[snaps.NAME] = release(snaps.NAME, 13)
    with pytest.raises(ValueError, match="changed"):
        snaps.apply(snaps.Approval(action="install", plan=original))
    assert not existing and not calls
    monkeypatch.setattr(snaps, "installed", lambda name: "1.3-debian")
    assert snaps.prepare("install").outcome == Outcome.BLOCKED
    monkeypatch.setattr(snaps, "installed", lambda name: None)
    existing[snaps.NAME] = remote[snaps.NAME]
    assert snaps.prepare("update").outcome == Outcome.BLOCKED
    assert snaps.prepare("uninstall").outcome == Outcome.NO_CHANGES
    owned = Resource(
        kind="snap",
        identity=snaps.NAME,
        ownership="created",
        cleanup=True,
        after=remote[snaps.NAME].model_dump_json(),
    )
    with ownership_context((owned,)):
        plan = snaps.prepare("uninstall")
    run = snaps.cli
    monkeypatch.setattr(
        snaps, "cli", lambda *a, **kw: "invalid snapshot" if a[0] == "save" else run(*a, **kw)
    )
    with pytest.raises(ValueError, match="snapshot"):
        snaps.apply(snaps.Approval(action="uninstall", plan=plan))
    assert existing[snaps.NAME] and ("remove", snaps.NAME) not in calls


def test_store_rejects_publisher_confinement_and_false_architecture(monkeypatch):
    sample = release(snaps.NAME)
    body = {
        "snap-id": sample.snap_id,
        "name": sample.name,
        "snap": {"publisher": {"id": snaps.PUBLISHER, "validation": "verified"}},
        "channel-map": [
            {
                "revision": sample.revision,
                "version": sample.version,
                "download": {"sha3-384": sample.sha3},
                "confinement": "strict",
                "snap-yaml": (
                    "base: core24\nplugs:\n  gtk:\n"
                    "    default-provider: gtk-common-themes\n  audio:\n"
                ),
                "channel": {
                    "track": "latest",
                    "risk": "stable",
                    "name": "stable",
                    "architecture": "amd64",
                },
            }
        ],
    }
    original = httpx.Client

    def client(**kwargs):
        def serve(req):
            assert req.url.host == "api.snapcraft.io" and req.url.path.endswith("/" + snaps.NAME)
            return httpx.Response(200, json=body)

        return original(transport=httpx.MockTransport(serve), **kwargs)

    monkeypatch.setattr(snaps.httpx, "Client", client)
    candidate, spec = snaps.latest(snaps.NAME, "amd64")
    assert candidate == sample and spec.requirements() == {
        "core24": set(),
        "gtk-common-themes": {"gtk"},
    }
    with pytest.raises(ValueError):
        snaps.latest(snaps.NAME, "arm64")
    body["snap"]["publisher"]["id"] = "external"
    with pytest.raises(ValueError):
        snaps.latest(snaps.NAME, "amd64")
    body["snap"]["publisher"]["id"] = snaps.PUBLISHER
    body["channel-map"][0]["confinement"] = "classic"
    with pytest.raises(ValueError):
        snaps.latest(snaps.NAME, "amd64")


@pytest.mark.parametrize(
    "arg", ["$(shell touch x)", " leading", "/other", "name\nHidden=true", "--flag=value", "%u"]
)
def test_launchers_accept_only_fixed_argv_words(tmp_path, monkeypatch, arg):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    with pytest.raises(ValueError, match="fixed native"):
        launcher_file(
            LauncherProfile(profile_id="fixture", name="Fixture"),
            "telegram",
            "/usr/bin/snap",
            ("run", arg),
        )


def test_interrupted_create_cannot_adopt_an_independent_install(daemon):
    import asyncio

    from setup_plugin_telegram import Plugin, Settings

    from setup_system_sdk import Inputs

    db, remote, _ = daemon
    plugin = Plugin()
    plan = plugin.prepare("install", Settings(), Inputs())
    db.update(remote)
    assert snaps.verify(plan).outcome == Outcome.COMPLETE
    result = asyncio.run(plugin.verify(plan, Settings(), Inputs()))
    assert result.outcome == Outcome.NEEDS_USER_ACTION and not result.resources


def test_old_debian_receipt_and_launcher_profile_after_plugin_upgrade(monkeypatch, tmp_path):
    import asyncio
    from uuid import uuid4

    from pydantic import ValidationError
    from setup_plugin_telegram import Plugin, Settings

    from setup_system.resource_ledger import ResourceLedger
    from setup_system_sdk import Inputs, Plan, Result, apt

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    plugin = Plugin()
    settings = Settings.resolve(
        profile={}, saved=Settings.migrate({"startup": "tray"}, 1), overrides={}
    )
    assert settings.startup == "tray"
    source = Resource(
        kind="package", identity=snaps.NAME, ownership="created", after="5.7", cleanup=True
    )
    external = source.model_copy(update={"ownership": "preexisting", "cleanup": False})

    def query(argv, root):
        if argv[0].endswith("dpkg-query"):
            return "install|||amd64"
        assert argv == ["/usr/bin/apt-get", "--simulate", "remove", "--", snaps.NAME]
        return "Remv telegram-desktop [5.7]\n"

    monkeypatch.setattr(apt, "query", query)
    monkeypatch.setattr(apt, "installed", lambda name: "5.7")
    monkeypatch.setattr(apt, "current_target", lambda: ("amd64", True))
    policy = apt.Packages.model_validate_json((plugin.package_folder / "apt.json").read_bytes())
    assert apt.resolve(tmp_path, tmp_path, plugin.package_folder, policy).outcome == Outcome.BLOCKED
    with pytest.raises(ValidationError):
        apt.Packages(names=())
    with ownership_context((external,)):
        assert (
            plugin.prepare("remove-debian-package", settings, Inputs()).outcome
            == Outcome.NO_CHANGES
        )

    async def upgrade():
        ledger = ResourceLedger(tmp_path / "resources.sqlite3")
        old = uuid4()
        await ledger.prepare(
            old, "telegram", "install", "1.0.0", Plan(summary="Debian Telegram", resources=[source])
        )
        await ledger.finish(
            old,
            Result(outcome=Outcome.COMPLETE, message="Confirmed installation", resources=[source]),
        )
        with ownership_context(tuple(await ledger.holdings("telegram"))):
            assert plugin.prepare("uninstall", settings, Inputs()).outcome == Outcome.NO_CHANGES
            snapshot = plugin.prepare("remove-debian-package", settings, Inputs())
            assert [(r.identity, r.before, r.effect) for r in snapshot.resources] == [
                (snaps.NAME, "5.7", "remove")
            ]
            removing = uuid4()
            await ledger.prepare(removing, "telegram", "remove-debian-package", "1.1.0", snapshot)
            await ledger.finish(
                removing,
                Result(
                    outcome=Outcome.COMPLETE,
                    message="Verified removal",
                    resources=snapshot.resources,
                ),
            )
        assert not await ledger.holdings("telegram")

    asyncio.run(upgrade())


def test_installed_shared_provider_with_missing_content_is_never_refreshed(daemon, monkeypatch):
    existing, remote, calls = daemon
    existing["runtime"] = remote["runtime"]
    plan = snaps.prepare("install")
    assert plan.outcome == Outcome.READY
    monkeypatch.setattr(snaps, "local_content", lambda name: set())
    refusal = snaps.prepare("install")
    assert refusal.outcome == Outcome.BLOCKED and "runtime" in refusal.summary
    with pytest.raises(snaps.MissingContentError):
        snaps.apply(snaps.Approval(action="install", plan=plan))
    assert all(call[0] == "known" for call in calls)
    assert list(existing) == ["runtime"]


def test_native_content_query_filters_other_providers(monkeypatch):
    def read(endpoint, **opts):
        assert endpoint == "connections" and opts == {
            "select": "all",
            "snap": "runtime",
            "interface": "content",
        }
        return (
            b'{"type":"sync","result":{"slots":['
            b'{"snap":"runtime","interface":"content","attrs":{"content":"icons"}},'
            b'{"snap":"another","interface":"content","attrs":{"content":"themes"}}]}}'
        )

    monkeypatch.setattr(snaps, "daemon_data", read)
    assert snaps.local_content("runtime") == {"icons"}


def test_overview_and_check_require_the_same_signed_strict_revision(daemon, monkeypatch):
    import asyncio

    from setup_plugin_telegram import Plugin, Settings

    from setup_system_sdk import Inputs

    existing, remote, _ = daemon
    existing[snaps.NAME] = remote[snaps.NAME]
    monkeypatch.setattr(
        snaps,
        "local",
        lambda: {snaps.NAME: native(remote[snaps.NAME]).model_copy(update={"devmode": True})},
    )
    backend = Plugin()
    assert asyncio.run(backend.inspect(Settings())).outcome == Outcome.NEEDS_USER_ACTION
    assert backend.prepare("check", Settings(), Inputs()).outcome == Outcome.NEEDS_USER_ACTION
