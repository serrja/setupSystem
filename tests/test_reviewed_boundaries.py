"""Exercise confirmed retirement, privilege inputs and interrupted local transactions."""

import asyncio
import contextvars
import socket
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr
from test_plugin_lifecycle import activation, resource

from setup_system import authorization, cleanup, extension_lifecycle, plugin_manager
from setup_system.application.rollback import RecoveryPlan
from setup_system.configuration import Settings
from setup_system.extension_lifecycle import removable, retire, seal_generation
from setup_system.infrastructure.managed_files import apply_files
from setup_system.plugin_manager import PluginManager, Snapshot
from setup_system.resource_ledger import ResourceLedger
from setup_system_sdk import Mutation, Outcome, Plan, Resource, Result
from setup_system_sdk.protocol import Reply


def test_ssh_and_secret_service_are_user_only(home, monkeypatch, tmp_path):
    # Linux limits the native AF_UNIX name. A relative bind keeps the fixture portable.
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    monkeypatch.chdir(runtime)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    bus, ssh = runtime / "bus", runtime / "agent"
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=" + str(bus))
    monkeypatch.setenv("SSH_AUTH_SOCK", str(ssh))
    with socket.socket(socket.AF_UNIX) as one, socket.socket(socket.AF_UNIX) as two:
        one.bind("bus")
        two.bind("agent")
        assert authorization.ssh_socket() == {"SSH_AUTH_SOCK": str(ssh)}
        assert authorization.session_environment()["DBUS_SESSION_BUS_ADDRESS"].endswith("/bus")
        assert "DBUS_SESSION_BUS_ADDRESS" not in authorization._environment()
        assert "SSH_AUTH_SOCK" not in authorization._environment()
        state = activation(home)
        assert "SSH_AUTH_SOCK" not in plugin_manager.worker_environment(state, "configure")
        state.manifest.actions[0].permissions.append("account-keys")
        assert plugin_manager.worker_environment(state, "configure")["SSH_AUTH_SOCK"] == str(ssh)
        monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "tcp:host=example.invalid")
        assert "DBUS_SESSION_BUS_ADDRESS" not in authorization.session_environment()


def test_privileged_make_receives_only_packaged_includes(home, monkeypatch):
    state = activation(home)
    state.manifest.actions[0].permissions.append("root")
    state.manifest.permissions.append("root")
    root = home / "installed"
    resources = root / "plugins/demo/fixture"
    resources.mkdir(parents=True)
    (resources / "Makefile").touch()
    (root / "sdk").mkdir()
    monkeypatch.setattr(plugin_manager, "INSTALLED", root)
    monkeypatch.setattr(plugin_manager, "verify_installation", lambda folder: None)
    monkeypatch.setattr(
        plugin_manager,
        "builtins",
        lambda folder: [
            SimpleNamespace(manifest=state.manifest, release=SimpleNamespace(digest=state.digest))
        ],
    )
    with pytest.raises(ValueError, match="incomplete"):
        plugin_manager.protected_resources(state)
    (root / "sdk/environment.mk").touch()

    async def native(folder, args, env, cancel, phase, take, **kwargs):
        assert folder == resources
        assert [Path(args[i + 1]) for i, a in enumerate(args) if a == "-f"] == [
            root / "sdk/environment.mk",
            resources / "Makefile",
        ]
        assert (
            "DBUS_SESSION_BUS_ADDRESS" not in env
            and env["SETUP_SYSTEM_INTERPRETER"] == "/usr/bin/python3"
        )
        result = Path(env["SETUP_SYSTEM_RESULT"])
        result.write_text(Result(outcome=Outcome.COMPLETE, message="Verified").model_dump_json())
        result.chmod(0o600)
        return 0

    monkeypatch.setattr(plugin_manager, "authenticated_make", native)
    manager = PluginManager(state_root=home / "state")
    snap = Snapshot(
        plugin_id="demo",
        digest=state.digest,
        mutation=Mutation(
            action_id="configure", inputs={}, settings={}, plan=Plan(summary="Review")
        ),
    )
    assert (
        asyncio.run(
            manager.runner.execute(state, snap, authorization.BecomePassword("fixture"))
        ).outcome
        is Outcome.COMPLETE
    )


def test_cleanup_restores_each_revision_and_preserves_later_edits(home):
    path = home / "project" / "config"
    path.parent.mkdir()
    path.write_bytes(b"original")
    from setup_system.domain.receipts import digest

    first = apply_files({path: b"first"}, expected={path: digest(b"original")}).name
    second = apply_files({path: b"second"}).name
    states = {}
    latest = RecoveryPlan.projected(second, states)
    older = RecoveryPlan.projected(first, states)
    assert latest.entries == older.entries == ((str(path), "would-restore"),)
    path.write_bytes(b"manual")
    assert latest.apply().outcome is Outcome.BLOCKED and path.read_bytes() == b"manual"
    path.write_bytes(b"second")
    assert latest.apply().outcome is Outcome.COMPLETE and path.read_bytes() == b"first"
    assert older.apply().outcome is Outcome.COMPLETE and path.read_bytes() == b"original"


def test_receipt_survives_failed_worker_and_can_reconcile(home, monkeypatch):
    manager = PluginManager(state_root=home / "state")
    state = activation(home)
    row = resource()
    plan = Plan(summary="Review", resources=[row])
    receipt = "20261125T080000Z-abcdea12"

    async def select(plugin):
        return state

    calls = []

    async def read(state, request, options):
        if request.method == "plan":
            return Reply(plan=plan)
        calls.append(request.method)
        if len(calls) == 1:
            raise ValueError("worker unavailable")
        return Reply(result=Result(outcome=Outcome.COMPLETE, message="Read back", resources=[row]))

    async def write(*args):
        return Result(outcome=Outcome.CHANGED, message="Published", recovery_id=receipt)

    monkeypatch.setattr(manager, "activation", select)
    monkeypatch.setattr(plugin_manager, "query", read)
    monkeypatch.setattr(manager.runner, "execute", write)

    async def run():
        snap = await manager.preview("demo", "configure", {}, {})
        assert (await manager.apply(snap)).outcome is Outcome.UNKNOWN_OUTCOME
        assert await manager.ledger.registered_receipts() == {receipt}
        assert (await manager.reconcile("demo"))[0].outcome is Outcome.COMPLETE
        assert await manager.ledger.recoveries("demo") == [(str(snap.operation_id), receipt)]
        assert calls == ["verify", "verify"]

    asyncio.run(run())


def test_writer_serializes_sessions_and_releases_on_exit(home):
    one = ResourceLedger(home / "state/ledger")
    two = ResourceLedger(one.path)

    async def probe():
        async with two.writer("demo"):
            return True

    async def run():
        async with one.writer("demo"):
            assert await probe()
            task = asyncio.create_task(probe(), context=contextvars.Context())
            with pytest.raises(ValueError, match="another session"):
                await task
        assert await probe()

    asyncio.run(run())


def test_local_secret_crash_reconciles_without_replay_or_secret_journal(home, monkeypatch):
    manager = PluginManager(state_root=home / "state")
    references = set()

    async def exists(plugin, ref):
        return ref in references

    async def partial(plugin, value, ref):
        references.add(ref)
        raise RuntimeError("channel closed")

    async def remove(plugin, ref):
        references.remove(ref)

    monkeypatch.setattr(cleanup, "secret_exists", exists)
    monkeypatch.setattr(cleanup, "store_secret", partial)
    monkeypatch.setattr(cleanup, "forget_secret", remove)

    async def run():
        with pytest.raises(RuntimeError, match="channel closed"):
            await cleanup.save_keyring_entry(manager, "demo", SecretStr("fake-only"))
        pending = await manager.ledger.pending("demo")
        assert len(pending) == 1 and "fake-only" not in pending[0]
        assert (await manager.reconcile("demo"))[0].outcome is Outcome.COMPLETE
        (row,) = await manager.ledger.holdings("demo")
        await cleanup.remove_keyring_entry(manager, "demo", row)
        assert not references and not await manager.ledger.pending("demo")
        assert not await manager.ledger.holdings("demo")

    asyncio.run(run())


def test_locally_saved_profile_crash_reconciles_and_preserves_divergence(home):
    manager = PluginManager(state_root=home / "state")
    store = Settings()

    async def run():
        before = await store.read("demo")
        values = {"title": "A", "accounts": [{"name": "fixture"}]}
        row = Resource(
            kind="plugin_settings",
            identity=cleanup.profile_path(store, "demo", "default"),
            ownership="created",
            before=before.revision,
            source_sha256=cleanup._values_digest(values),
            cleanup=True,
        )

        async def prepare():
            await manager.ledger.prepare(
                uuid4(), "demo", "save-settings", "1", Plan(summary="Profile", resources=[row])
            )

        await prepare()
        assert (await manager.reconcile("demo"))[0].outcome is Outcome.CANCELLED
        await prepare()
        changed = await store.publish("demo", "default", {"title": "manual"}, 1, before.revision)
        assert (await manager.reconcile("demo"))[0].outcome is Outcome.PARTIAL
        await store.publish("demo", "default", values, 1, changed.revision)
        assert (await manager.reconcile("demo"))[0].outcome is Outcome.COMPLETE
        (held,) = await manager.ledger.holdings("demo")
        assert (await store.read("demo")).revision == held.after

    asyncio.run(run())


def test_retirement_resumes_after_partial_deletion_and_detects_added_files(home, monkeypatch):
    env = home / "envs"
    root = env / "demo"
    gen = root / ("1" * 32)
    gen.mkdir(parents=True)
    (gen / "files").mkdir()
    (gen / "files" / "a").write_text("a")
    (gen / "files" / "b").write_text("b")
    seal_generation(gen)
    state = activation(home)
    (root / "active.json").write_text(
        extension_lifecycle._Pointer(
            generation=gen.name, digest=state.digest, plugin_id="demo"
        ).model_dump_json()
    )
    monkeypatch.setattr(extension_lifecycle, "_read_generation", lambda *_: state)
    entries = removable(env, "demo")
    import hashlib

    from setup_system.infrastructure import recorded_tree

    original = recorded_tree._discard
    count = 0

    def crash(claims, name, entry):
        nonlocal count
        if entry[3] in {hashlib.sha256(b"a").hexdigest(), hashlib.sha256(b"b").hexdigest()}:
            count += 1
            if count == 2:
                raise PermissionError("injected interruption")
        return original(claims, name, entry)

    with monkeypatch.context() as restricted:
        restricted.setattr(recorded_tree, "_discard", crash)
        with pytest.raises(PermissionError, match="interruption"):
            retire(env, "demo", entries)
    assert extension_lifecycle.retirement_manifest(root) == state.manifest
    other = gen / "files" / "later"
    other.write_text("independent edit")
    with pytest.raises(ValueError, match="changed"):
        retire(env, "demo", entries)
    assert other.read_text() == "independent edit"
    other.unlink()
    assert removable(env, "demo") == entries
    retire(env, "demo", entries)
    assert not root.exists()


def test_racing_file_replacement_during_retirement_is_preserved(home, monkeypatch):
    from setup_system.infrastructure import recorded_tree

    root = home / "code"
    root.mkdir()
    target = root / "owned"
    target.write_text("initial")
    records = recorded_tree.record_tree(root)
    original = recorded_tree.rename_new

    def race(source, name, dest, new):
        if name == "owned":
            changed = root / "new"
            changed.write_text("manual")
            changed.replace(target)
        original(source, name, dest, new)

    monkeypatch.setattr(recorded_tree, "rename_new", race)
    with pytest.raises(ValueError, match="preserved"):
        recorded_tree.remove_recorded(root, records)
    assert target.read_text() == "manual"


def test_generated_identity_must_match_native_and_later_worker_readback(home):
    manager = PluginManager(state_root=home / "state")
    row = resource(after=None)
    published = row.model_copy(update={"after": "native", "permissions": 0o600, "verified": True})

    async def run():
        ident = uuid4()
        await manager.ledger.prepare(
            ident, "demo", "create", "digest", Plan(summary="Generate", resources=[row])
        )
        await manager.ledger.applied(ident, observations=(published,))
        different = published.model_copy(update={"after": "later"})
        with pytest.raises(ValueError, match="changed"):
            await manager.ledger.finish(
                ident, Result(outcome=Outcome.COMPLETE, message="Read", resources=[different])
            )
        assert not await manager.ledger.holdings("demo")
        await manager.ledger.finish(
            ident, Result(outcome=Outcome.COMPLETE, message="Read", resources=[published])
        )
        assert (await manager.ledger.holdings("demo"))[0].after == "native"

    asyncio.run(run())


def test_cancelled_install_releases_only_provisional_dependencies(home):
    ledger = ResourceLedger(home / "db/ledger")

    async def run():
        first = uuid4()
        await ledger.prepare(
            first,
            "consumer",
            "install",
            "identity",
            Plan(summary="Install"),
            dependencies=("base",),
        )
        assert await ledger.dependents("base") == ["consumer"]
        await ledger.finish(first, Result(outcome=Outcome.CANCELLED, message="No changes"))
        assert not await ledger.dependents("base")
        installed = uuid4()
        await ledger.prepare(
            installed,
            "consumer",
            "install",
            "identity",
            Plan(summary="Install"),
            dependencies=("base",),
        )
        await ledger.finish(installed, Result(outcome=Outcome.COMPLETE, message="Complete"))
        update = uuid4()
        await ledger.prepare(
            update,
            "consumer",
            "update",
            "identity",
            Plan(summary="Update"),
            dependencies=("base", "extra"),
        )
        await ledger.finish(update, Result(outcome=Outcome.BLOCKED, message="Cancelled"))
        assert await ledger.dependents("base") == ["consumer"] and not await ledger.dependents(
            "extra"
        )

    asyncio.run(run())


def test_interrupted_profile_removal_retains_edited_staging_copy(home):
    from setup_system.configuration.store import reconcile_removed_profile

    store = Settings()

    async def setup():
        empty = await store.read("demo")
        return await store.publish("demo", "default", {"title": "fixture"}, 1, empty.revision)

    existing = asyncio.run(setup())
    path = Path(cleanup.profile_path(store, "demo", "default"))
    operation = uuid4()
    staged = path.with_name(".removing-" + operation.hex)
    path.rename(staged)
    assert reconcile_removed_profile(path, operation, existing.revision)
    assert not staged.exists()
    existing = asyncio.run(setup())
    path.rename(staged)
    staged.write_text("manual")
    with pytest.raises(ValueError, match="changed"):
        reconcile_removed_profile(path, operation, existing.revision)
    assert staged.read_text() == "manual"


def test_lost_native_response_cannot_adopt_later_generated_object(home, monkeypatch):
    manager = PluginManager(state_root=home / "state")
    state = activation(home)
    row = resource(after=None)
    plan = Plan(summary="Create unique local identity", resources=[row])

    async def select(plugin):
        return state

    async def query(state, request, options):
        if request.method == "plan":
            return Reply(plan=plan)
        appeared = row.model_copy(update={"after": "later-edit", "verified": True})
        return Reply(
            result=Result(
                outcome=Outcome.COMPLETE, message="Current identity", resources=[appeared]
            )
        )

    async def lost(*args):
        raise OSError("Native result lost")

    monkeypatch.setattr(manager, "activation", select)
    monkeypatch.setattr(plugin_manager, "query", query)
    monkeypatch.setattr(manager.runner, "execute", lost)

    async def run():
        snap = await manager.preview("demo", "configure", {}, {})
        assert (await manager.apply(snap)).outcome is Outcome.UNKNOWN_OUTCOME
        assert (await manager.reconcile("demo"))[0].outcome is Outcome.PARTIAL
        assert not await manager.ledger.holdings("demo")
        assert len(await manager.ledger.pending("demo")) == 1

    asyncio.run(run())
