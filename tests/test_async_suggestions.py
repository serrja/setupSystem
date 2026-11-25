"""Exercise real bounded directory completion and delayed results."""

import asyncio
import threading
from pathlib import Path

from setup_system.configuration import Settings
from setup_system.suggestions import ProjectChoices, Suggestions


def test_latest_edit_wins_while_directory_read_is_pending(tmp_path, monkeypatch):
    async def scenario():
        entered = threading.Event()
        release = threading.Event()
        requests = []
        first = tmp_path / "first"
        second = tmp_path / "second"
        for directory in (first, second):
            directory.mkdir()
            (directory / "A B é").mkdir()
        source = Suggestions(
            Path(__file__).resolve().parents[1] / "config", tmp_path, Settings.application()
        )
        original = source._scan

        def delayed(directory):
            requests.append(directory)
            if directory == first:
                entered.set()
                assert release.wait(3)
            return original(directory)

        monkeypatch.setattr(source, "_scan", delayed)
        choices = ProjectChoices(source)
        old = asyncio.create_task(choices.get_suggestion(str(first) + "/A"))
        assert await asyncio.to_thread(entered.wait, 2)
        current = asyncio.create_task(choices.get_suggestion(str(second) + "/A"))
        await asyncio.sleep(0)
        release.set()
        assert await old is None
        assert await current == str(second / "A B é")
        assert await choices.get_suggestion(str(second) + "/A ") == str(second / "A B é")
        assert requests == [first, second]
        assert await choices.get_suggestion("") is None

    asyncio.run(scenario())


def test_quick_edits_use_one_scan_and_return_bounded_results(tmp_path, monkeypatch):
    async def scenario():
        for idx in range(160):
            (tmp_path / f"folder {idx}").mkdir()
        source = Suggestions(tmp_path, tmp_path, Settings.application())
        choices = ProjectChoices(source)
        scans = []
        original = source._scan

        def counted(directory):
            scans.append(directory)
            return original(directory)

        monkeypatch.setattr(source, "_scan", counted)
        replies = await asyncio.gather(
            *(choices.get_suggestion(str(tmp_path) + suffix) for suffix in ("/f", "/fo", "/fol"))
        )
        assert replies[:2] == [None, None]
        assert replies[-1].startswith(str(tmp_path / "folder "))
        assert 0 < len(await source.project_values(str(tmp_path) + "/")) <= 128
        assert scans == [tmp_path]

    asyncio.run(scenario())


def test_a_slow_previous_directory_does_not_delay_the_latest_query(tmp_path, monkeypatch):
    async def scenario():
        first, latest = tmp_path / "first", tmp_path / "latest"
        first.mkdir()
        latest.mkdir()
        (latest / "answer").mkdir()
        source = Suggestions(tmp_path, tmp_path, Settings.application())
        started, release = threading.Event(), threading.Event()
        scan = source._scan

        def scan_directory(path):
            if path == first:
                started.set()
                assert release.wait(4)
            return scan(path)

        monkeypatch.setattr(source, "_scan", scan_directory)
        choices = ProjectChoices(source)
        stale = asyncio.create_task(choices.get_suggestion(str(first) + "/"))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            fresh = choices.get_suggestion(str(latest) + "/a")
            assert await asyncio.wait_for(fresh, 1) == str(latest / "answer")
            assert await stale is None
        finally:
            release.set()

    asyncio.run(scenario())


def test_directory_completion_obeys_the_application_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("SETUP_SYSTEM_SUGGESTION_RESULTS", "1")
    for name in ("alpha", "beta", "gamma"):
        (tmp_path / name).mkdir()
    choices = Suggestions(tmp_path, tmp_path, Settings.application())
    assert len(asyncio.run(choices.project_values(str(tmp_path) + "/"))) == 1
