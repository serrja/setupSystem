"""Exercise first activation through verified full cleanup using real private workers and Make."""

import asyncio
import hashlib
import subprocess
from pathlib import Path

from test_extensions import descriptor, sdk_wheels, wheel

from setup_system.cleanup import apply_cleanup, preview_cleanup, save_profile
from setup_system.configuration import Settings
from setup_system.extensions import (
    Release,
    ReleaseDocument,
    ReleaseRecord,
    TrustPolicy,
    Wheel,
    install_release,
)
from setup_system.plugin_manager import PluginManager
from setup_system_sdk import Action, Inputs, Manifest, Outcome, PluginSettings


class Preferences(PluginSettings):
    title: str = "Example"


def test_packaged_extension_lifecycle_retains_other_user_data(tmp_path, monkeypatch):
    home = tmp_path / "Synthetic home 資料"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    for area in ("DATA", "STATE", "CONFIG", "CACHE"):
        monkeypatch.setenv("XDG_" + area + "_HOME", str(home / area.lower()))
    action_ids = ("install", "configure", "record", "uninstall")
    manifest = Manifest.model_validate(
        descriptor(
            "sample",
            entry_point="ext_sample:Example",
            settings_schema=Preferences.model_json_schema(),
            actions=[
                Action(
                    id=name,
                    label=name.title(),
                    target=name,
                    cleanup_phase="program" if name == "uninstall" else None,
                ).model_dump(mode="json")
                for name in action_ids
            ],
            input_schemas={name: Inputs.model_json_schema() for name in action_ids},
        )
    )
    implementation = (Path(__file__).parent / "fixtures/lifecycle_worker.py").read_text()
    artifact = wheel(
        tmp_path,
        "sample-extension",
        "1.0",
        "ext_sample",
        implementation,
        manifest=manifest.model_dump(mode="json"),
        requires=["setup-system-sdk"],
        extras={
            "ext_sample/Makefile": (
                ".PHONY: "
                + " ".join(action_ids)
                + "\n"
                + " ".join(action_ids)
                + ":\n"
                + '\t@"$'
                + '${SETUP_SYSTEM_INTERPRETER}" -I -B -m ext_sample.run "$@"\n'
            ).encode(),
            "ext_sample/run.py": implementation.encode(),
        },
    )
    release = Release(
        ReleaseDocument(
            releases=[
                ReleaseRecord(
                    manifest=manifest,
                    wheels=tuple(
                        Wheel(name=p.name, sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                        for p in (artifact, *sdk_wheels(tmp_path))
                    ),
                )
            ]
        )
        .model_dump_json()
        .encode()
    )
    private_value = "Personal profile 資料"
    personal = home / "notes"
    personal.write_text("user's independent data")

    async def exercise():
        manager = PluginManager(env_root=home / "envs", state_root=home / "state")
        store = Settings()
        await install_release(
            release,
            policy=TrustPolicy(digests={release.digest}),
            env_root=manager.env_root,
            wheelhouse=tmp_path,
        )
        assert not (home / "fixture-program").exists()
        install = await manager.preview("sample", "install", {}, {})
        assert install.mutation.plan.outcome is Outcome.READY
        assert (await manager.apply(install)).outcome is Outcome.COMPLETE
        assert (
            subprocess.check_output([str(home / "fixture-program")], text=True).strip()
            == "fixture 1.0"
        )
        saved = await store.read("sample")
        await save_profile(
            manager, store, "sample", "default", {"title": private_value}, 1, saved.revision
        )
        assert not (home / "fixture-profile").exists()
        values = (await store.read("sample")).values
        configuration = await manager.preview("sample", "configure", {}, {}, saved=values)
        assert (await manager.apply(configuration)).outcome is Outcome.COMPLETE
        assert (home / "fixture-profile").read_text() == private_value
        repeated = await manager.preview("sample", "configure", {}, {}, saved=values)
        assert repeated.mutation.plan.outcome is Outcome.NO_CHANGES
        action = await manager.preview("sample", "record", {}, {}, saved=values)
        assert (await manager.apply(action)).outcome is Outcome.COMPLETE
        assert (home / "fixture-record").read_text() == "checked"
        cleanup = await preview_cleanup(manager, store, "sample", values)
        assert not cleanup.blockers
        assert [s.snapshot.mutation.action_id for s in cleanup.steps] == ["uninstall"]

        async def authorize(action):
            assert not action.permissions
            return None

        complete = await apply_cleanup(manager, store, cleanup, authorize)
        assert complete.outcome is Outcome.COMPLETE, complete
        assert not await manager.ledger.holdings("sample")
        assert not (manager.env_root / "sample").exists() and not list(home.glob("fixture-*"))
        assert not (await store.read("sample")).values
        assert personal.read_text() == "user's independent data"
        history = await manager.journal.history("sample")
        assert {e.action_id for e in history} >= {
            "install",
            "configure",
            "record",
            "uninstall",
            "full-cleanup",
        }
        assert all(private_value not in e.model_dump_json() for e in history)

    asyncio.run(exercise())
