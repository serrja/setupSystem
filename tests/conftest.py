"""Private user state and application components for host and SDK contract tests."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from setup_system.configuration.settings import Settings
from setup_system.infrastructure.database.engine import create_engine, session_factory
from setup_system.infrastructure.database.migrations import runtime_schema


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import os

    path = tmp_path / "home"
    path.mkdir(mode=0o700)
    # Drop inherited tokens, agent sockets, display servers and plugin overrides.
    for key in tuple(os.environ):
        if key not in {"PATH", "LANG", "LC_ALL", "TMPDIR"}:
            monkeypatch.delenv(key)
    monkeypatch.setenv("HOME", str(path))
    for key in ("CONFIG", "STATE", "CACHE", "DATA"):
        directory = path / key.lower()
        directory.mkdir(mode=0o700)
        monkeypatch.setenv(f"XDG_{key}_HOME", str(directory))
    runtime = path / "runtime"
    runtime.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return path


@pytest_asyncio.fixture
async def engine(home: Path) -> AsyncIterator[AsyncEngine]:
    database = create_engine(Settings())
    try:
        async with runtime_schema(database):
            yield database
    finally:
        await database.dispose()


@pytest.fixture
def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return session_factory(engine)
