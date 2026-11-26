import json
from uuid import uuid4

import anyio
import pytest
from setup_plugin_debian import providers
from setup_plugin_debian.adapters.platform import target
from setup_system_sdk.context import PluginContext
from setup_system_sdk.operations import Executor
from setup_system_sdk.providers import CapabilityRequest


def test_architecture_must_match_inventory() -> None:
    facts = {"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"}
    assert target(facts, "x86_64", "amd64") == ("amd64", True)
    assert target(facts, "x86_64", "arm64") == ("amd64", False)
    assert not target({}, "x86_64", "amd64")[1]


@pytest.mark.asyncio
async def test_unsupported_provider_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers, "current_target", lambda: ("sample", False))
    executor = Executor(
        plugin_id="debian", version="1.0.0", release_digest="1" * 64, generation="test"
    )

    async def ignore(event: object) -> None:
        pass

    context = PluginContext(uuid4(), executor, anyio.Event(), ignore)
    req = CapabilityRequest(
        capability="system.packages",
        step_key="packages",
        payload={"consumer": "sample", "action": "install"},
    )
    facts = await providers.PackageProvider().inspect(req, context)
    assert isinstance(facts["proposal"], dict) and facts["proposal"]["outcome"] == "blocked"
    with pytest.raises(ValueError, match="requires Debian"):
        await providers.PackageProvider().plan(req, context)


def test_generated_provider_contract() -> None:
    from importlib.resources import files

    manifest = json.loads(files("setup_plugin_debian").joinpath("manifest.json").read_text())
    provided = next(c for c in manifest["provides"] if c["id"] == "system.packages")
    assert provided["request_schema"] == providers.PackageProvider.request_model.model_json_schema()
