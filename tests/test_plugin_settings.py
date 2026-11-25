"""Check source precedence, schema restrictions and revision-safe profile storage."""

import asyncio
from typing import ClassVar

import pytest
from pydantic import Field, ValidationError

from setup_system.configuration import Settings
from setup_system.configuration.store import profiles
from setup_system_sdk import Inputs, PluginSettings, UIHints


class Demo(PluginSettings):
    count: int = Field(
        default=1,
        ge=1,
        le=10,
        json_schema_extra={"ui": UIHints(widget="number").model_dump(exclude_none=True)},
    )
    environment_fields: ClassVar[dict[str, str]] = {"DEMO_COUNT": "count"}


def test_source_precedence_and_closed_inputs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DEMO_COUNT=9\n")
    assert Demo.resolve(profile={}, saved={}, overrides={}).count == 1
    assert Demo.resolve(profile={"count": 2}, saved={}, overrides={}).count == 2
    assert Demo.resolve(profile={"count": 2}, saved={"count": 3}, overrides={}).count == 3
    monkeypatch.setenv("DEMO_COUNT", "4")
    assert Demo.resolve(profile={}, saved={"count": 3}, overrides={}).count == 4
    assert Demo.resolve(profile={}, saved={}, overrides={"count": 5}).count == 5
    with pytest.raises(ValidationError):
        Demo.resolve(profile={}, saved={}, overrides={"count": 100})
    with pytest.raises(ValidationError):
        Inputs.model_validate({"extra": "unregistered"})


def test_profiles_use_cas_and_preserve_user_backup(tmp_path):
    store = Settings(tmp_path, tmp_path / "config")

    async def exercise():
        initial = await store.read("demo")
        saved = await store.publish("demo", "default", {"count": 4}, 1, initial.revision)
        assert (await store.read("demo")).values == {"count": 4}
        with pytest.raises(ValueError, match="changed"):
            await store.publish("demo", "default", {"count": 6}, 1, initial.revision)
        await store.publish("demo", "default", {"count": 7}, 1, saved.revision)
        assert len(list((store.directory / "plugins/demo").glob("*.bak-*"))) == 1
        assert profiles(store, "demo") == ["default"]
        with pytest.raises(ValueError):
            await store.read("../other")

    asyncio.run(exercise())
