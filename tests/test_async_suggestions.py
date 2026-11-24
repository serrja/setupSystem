"""Exercise real bounded directory completion and delayed results."""

import asyncio
import threading
from pathlib import Path

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
        source = Suggestions(Path(__file__).resolve().parents[1] / "config", tmp_path)
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
        source = Suggestions(tmp_path, tmp_path)
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
