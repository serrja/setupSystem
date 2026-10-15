---
name: python-project
description: Set up or update Python packaging, typing, lint and test tooling in an assigned repository; exclude business behavior and new domain tests.
---

# Python project tooling

Input: assigned repository and its existing contract. Keep its minimum Python and package layout unless the task needs a change. Use `pyproject.toml`, `uv lock` and `uv sync --locked`; use Ruff and strict mypy where applicable. Configure pytest without fake passing tests. Output exact commands and actual results. Preserve runtime dependencies, API and existing tests.
