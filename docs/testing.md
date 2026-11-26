# Testing

Run `uv run --locked pytest -n 2 tests` for host, SDK and contract tests. The fixtures create private HOME, XDG and temporary directories per test. Database tests use synthetic SQLite files. Async tests declare their asyncio marker and use an asyncio backend. Worker cancellation, signals and terminal behavior require real subprocesses.

Use `make plugins-check` to invoke package-owned checks. Installation, hardware and boot acceptance require an explicitly authorized disposable target. See [development](development.md).
