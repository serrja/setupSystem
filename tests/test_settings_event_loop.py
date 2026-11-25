"""Publishing a private TOML revision leaves the event loop available."""

import asyncio
import fcntl
import os
import threading

from setup_system.configuration import Settings
from setup_system.configuration import store as storage


def test_slow_profile_publication_has_safe_cancellation_boundary(tmp_path, monkeypatch):
    settings = Settings(tmp_path, tmp_path / "config")
    entered, release = threading.Event(), threading.Event()
    publish = storage.save_values

    def slow(*args):
        entered.set()
        assert release.wait(5)
        return publish(*args)

    monkeypatch.setattr(storage, "save_values", slow)

    async def exercise():
        initial = await settings.read("example")
        task = asyncio.create_task(
            settings.publish("example", "default", {"choice": "new"}, 1, initial.revision)
        )
        assert await asyncio.to_thread(entered.wait, 2)
        try:
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and not (await settings.read("example")).values
        finally:
            release.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert (await settings.read("example")).values == {"choice": "new"}

    asyncio.run(exercise())


def test_exclusive_directory_lock_does_not_block_profile_reads(tmp_path):
    settings = Settings(tmp_path, tmp_path / "config")

    async def exercise():
        revision = (await settings.read("demo")).revision
        first = await settings.publish("demo", "default", {"n": 1}, 1, revision)
        fd = os.open(settings.directory / "plugins/demo", os.O_DIRECTORY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            pending = asyncio.create_task(
                settings.publish("demo", "default", {"n": 2}, 1, first.revision)
            )
            assert (await asyncio.wait_for(settings.read("demo"), 1)).values == {"n": 1}
            assert not pending.done()
        finally:
            os.close(fd)
        assert (await pending).values == {"n": 2}

    asyncio.run(exercise())
