import json
import sys
import sysconfig
from pathlib import Path
from uuid import uuid4

import pytest
import setup_system_sdk
from setup_system_sdk import PluginSettings
from setup_system_sdk.enums.operation import Outcome, WorkerMethod
from setup_system_sdk.enums.ui import StorageScope
from setup_system_sdk.files.generations import seal_generation
from setup_system_sdk.manifests import Manifest
from setup_system_sdk.processes import ProcessRunner
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.forms import form_schema
from setup_system_sdk.schemas.wire import Request

from setup_system.bootstrap import runtime
from setup_system.infrastructure.plugins.environments import Activation
from setup_system.infrastructure.plugins.registry import PluginRegistry
from setup_system.infrastructure.plugins.workers import WorkerClient

IMPLEMENTATION = (Path(__file__).parent / "fixtures/workers" / "implementation.py.in").read_text()


def activate_fixture(
    home: Path,
    name: str = "fixture",
    *,
    implementation: str = IMPLEMENTATION,
    rollback: bool = False,
    provides: bool = False,
    requires: bool = False,
) -> Activation:
    stable = uuid4().hex
    root = home / "data/setup-system/v10/extensions" / name
    generation = root / stable
    env = generation / "venv"
    site = (
        env / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    )
    site.mkdir(parents=True, mode=0o700)
    (env / "bin").mkdir()
    (env / "bin/python").symlink_to(Path(sys.executable).resolve())
    (env / "pyvenv.cfg").write_text(
        f"home = {Path(sys.base_prefix) / 'bin'}\ninclude-system-site-packages = false\n"
    )
    # Link the runtime libraries exclusively inside this synthetic generation.
    for library in Path(sysconfig.get_path("purelib")).iterdir():
        if (
            library.name.startswith(("setup_system", "setup_plugin_", "_"))
            or library.suffix == ".pth"
        ):
            continue
        (site / library.name).symlink_to(library)
    (site / "setup_system_sdk").symlink_to(Path(setup_system_sdk.__file__).parent)
    pkg = site / f"setup_plugin_{name}"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(implementation)
    metadata_dir = site / f"setup_plugin_{name}-1.0.dist-info"
    metadata_dir.mkdir()
    (metadata_dir / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: setup-plugin-{name}\nVersion: 1.0\n"
    )
    entry = f"{pkg.name}:Plugin"
    (metadata_dir / "entry_points.txt").write_text(f"[setup_system.plugins]\n{name} = {entry}\n")
    manifest = Manifest.model_validate(
        dict(
            id=name,
            name=name,
            author="fixture",
            category="sample",
            kind="application",
            sdk=">=3,<4",
            entry_point=entry,
            version="1.0",
            source="https://example.org/repo",
            permissions=[],
            capabilities=[],
            dependencies=[],
            provides=[
                {
                    "id": "fixture.write",
                    "version": "1",
                    "request_schema": Inputs.model_json_schema(),
                }
            ]
            if provides
            else [],
            actions=[]
            if provides
            else [
                dict(
                    id="save",
                    label="Save",
                    supports_rollback=rollback,
                    mode="mutation",
                    target="save",
                    requires=([{"id": "fixture.write", "version": ">=1,<2"}] if requires else []),
                )
            ],
            settings_schema=form_schema(PluginSettings, StorageScope.SETTINGS),
            input_schemas={} if provides else {"save": form_schema(Inputs, StorageScope.ACTION)},
        )
    )
    for path in [pkg / "manifest.json", generation / "manifest.json"]:
        path.write_text(manifest.model_dump_json())
    seal_generation(generation)
    (root / "active.json").write_text(
        json.dumps({"plugin_id": name, "generation": stable, "digest": "d" * 64})
    )
    return PluginRegistry(root.parent, {}).resolve(name)


@pytest.mark.asyncio
async def test_independent_worker_preview_apply(home: Path) -> None:
    state = activate_fixture(home)
    async with runtime() as services:
        preview = await services.operations.preview(state.manifest.id, "save", {}, {})
        assert preview.plan.steps[0].executor == state.executor
        assert not (home / "marker").exists()
        with pytest.raises(ValueError, match="dispatch mode"):
            await WorkerClient(ProcessRunner(), 5).call(
                state, Request(plugin_id="fixture", method=WorkerMethod.EXECUTE)
            )
        result = await services.operations.apply(preview)
        assert result.outcome == "complete"
        assert (home / "marker").read_text() == "yes"
        assert (await services.recovery.history())[0][-1] == "verified"


PROVIDER = (Path(__file__).parent / "fixtures/workers" / "provider.py.in").read_text()

CONSUMER = (Path(__file__).parent / "fixtures/workers" / "consumer.py.in").read_text()


@pytest.mark.asyncio
async def test_provider_selection_and_pinned_execution(home: Path) -> None:
    activate_fixture(home, "consumer", implementation=CONSUMER, requires=True)
    async with runtime() as services:
        service = services.operations
        blocked = await service.preview("consumer", "save", {}, {})
        assert blocked.plan.outcome == "blocked"
        first = activate_fixture(home, "writer", implementation=PROVIDER, provides=True)
        snap = await service.preview("consumer", "save", {}, {})
        assert snap.plan.steps[0].executor == first.executor
        assert snap.dependencies == ("writer",)
        activate_fixture(home, "second", implementation=PROVIDER, provides=True)
        ambiguous = await service.preview("consumer", "save", {}, {})
        assert ambiguous.plan.outcome == "blocked"
        service.registry.selected["fixture.write"] = "writer"
        result = await service.apply(snap)
        assert result.outcome == "complete"
        assert (home / "provider-marker").read_text() == "done"


@pytest.mark.asyncio
async def test_recovery_skips_verified_execution(home: Path) -> None:
    from setup_system_sdk.enums.operation import Outcome
    from setup_system_sdk.operations import Receipt, Result

    state = activate_fixture(home)
    async with runtime() as services:
        snapshot = await services.operations.preview("fixture", "save", {}, {})
        (step,) = snapshot.plan.steps
        ledger = services.operations.ledger
        await ledger.prepare(snapshot)
        await ledger.mark_running(snapshot.operation_id, step)
        marker = home / "marker"
        marker.write_text("yes")
        before = marker.stat()
        receipt = Receipt(
            operation_id=snapshot.operation_id,
            step_key=step.step_key,
            executor=state.executor,
            result=Result(
                outcome=Outcome.CHANGED,
                message="User file published",
                resources=list(step.resources),
            ),
        )
        await ledger.record_applied(snapshot.operation_id, step, receipt)
        assert (
            await services.recovery.inspect(snapshot.operation_id)
        ).outcome == Outcome.NEEDS_USER_ACTION
        proposal = await services.recovery.plan(snapshot.operation_id)
        assert proposal.remaining == () and proposal.completed == (step.step_key,)
        assert (await services.recovery.apply(proposal)).outcome == Outcome.COMPLETE
        assert marker.stat().st_mtime_ns == before.st_mtime_ns


@pytest.mark.asyncio
async def test_confirmed_file_rollback(home: Path) -> None:
    implementation = (Path(__file__).parent / "fixtures/workers/files.py.in").read_text()
    state = activate_fixture(home, implementation=implementation, rollback=True)
    managed = home / "fixture-file"
    managed.write_bytes(b"original")
    managed.chmod(0o600)
    async with runtime() as services:
        original = await services.operations.preview(state.manifest.id, "save", {}, {})
        assert (await services.operations.apply(original)).outcome == Outcome.COMPLETE
        assert managed.read_bytes() == b"replacement"
        rollback = await services.recovery.rollback(original.operation_id)
        assert managed.read_bytes() == b"replacement"
        managed.write_bytes(b"later edit")
        with pytest.raises((RuntimeError, ValueError)):
            await services.operations.apply(rollback)
        assert managed.read_bytes() == b"later edit"
        managed.write_bytes(b"replacement")
        assert (await services.operations.apply(rollback)).outcome == Outcome.COMPLETE
        assert managed.read_bytes() == b"original"
        async with services.operations.ledger.uow.read() as repos:
            assert not await repos.resources.list_holdings(state.manifest.id)
