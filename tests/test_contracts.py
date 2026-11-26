"""Check request identity, bounded provider dispatch and protected execution grants."""

from pathlib import Path
from uuid import uuid4

import pytest
from packaging.requirements import Requirement
from packaging.version import Version
from setup_system_sdk.operations import Executor, StepPlan
from setup_system_sdk.protocol import CALL_LIMIT, Exchange, ProviderCallMessage, ProviderPayload
from setup_system_sdk.providers import CapabilityRequest

from setup_system.configuration.profiles import SettingsStore
from setup_system.infrastructure.plugins.environments import verify_dependencies
from setup_system.infrastructure.trust import TrustStore


def test_worker_rejects_replayed_and_excess_provider_calls() -> None:
    key = uuid4()
    exchange = Exchange(key)
    request = ProviderCallMessage(
        request_id=key,
        call_id=uuid4(),
        payload=ProviderPayload(
            method="inspect",
            request=CapabilityRequest(capability="sample.info", step_key="info", payload={}),
        ),
    )
    exchange.encode(request)
    with pytest.raises(ValueError, match="limits"):
        exchange.encode(request)
    for _ in range(CALL_LIMIT - 1):
        exchange.encode(request.model_copy(update={"call_id": uuid4()}))
    with pytest.raises(ValueError, match="limits"):
        exchange.encode(request.model_copy(update={"call_id": uuid4()}))


def test_user_manifest_cannot_authorize_native_execution(home: Path) -> None:
    identity = Executor(
        plugin_id="fixture", version="1", generation="candidate", release_digest="a" * 64
    )
    step = StepPlan(step_key="config", executor=identity, handler="publish", contract_version="1")
    (home / "fixture.json").write_text('{"allowed_handlers":["publish"]}')
    with pytest.raises(ValueError, match="root-owned"):
        TrustStore(home).resolve(step)


@pytest.mark.asyncio
async def test_historical_profile_stays_revision_checked(home: Path) -> None:
    path = home / "config/setup-system/plugins/fixture/work.toml"
    path.parent.mkdir(parents=True, mode=0o700)
    path.write_text('schema_version = 1\n[values]\ntitle = "existing"\n')
    path.chmod(0o600)
    store = SettingsStore(profile="work")
    saved = await store.read("fixture")
    assert saved.values == {"title": "existing"}
    await store.write("fixture", {"title": "new"}, 2, saved.revision)
    with pytest.raises(ValueError, match="changed"):
        await store.write("fixture", {}, 1, saved.revision)
    assert (await store.read("fixture")).version == 2
    assert len(list(path.parent.glob("work.toml.bak-*"))) == 1


def test_reviewed_wheel_closure_matches_metadata() -> None:
    metadata = {
        "sample": (Version("1"), (Requirement("dep[extra]>=1"),)),
        "dep": (Version("2"), (Requirement('leaf; extra == "extra"'),)),
        "leaf": (Version("1"), ()),
    }
    verify_dependencies("sample", metadata)
    with pytest.raises(ValueError, match="closure"):
        verify_dependencies("sample", {**metadata, "unrelated": (Version("2"), ())})
    with pytest.raises(ValueError, match="lacks"):
        verify_dependencies("sample", {**metadata, "dep": (Version("0.1"), ())})
