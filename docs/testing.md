# Testing and delivery

Run `uv sync --locked`, `uv run --locked ruff check .`, `uv run --locked ruff format --check .`, `uv run --locked mypy` and `uv run --locked pytest --cov --cov-report=term-missing --cov-report=json --cov-report=xml`. Coverage includes the complete `setup_system` package and stdlib bootstrap, including unimported modules. Statement/branch gate is 100%, precision two decimals; a passing test count does not imply that gate is met.

Tests use synthetic temporary homes, Git repositories and files. Backup/recovery tests assert private permissions, hashes, conflict preservation and symlink/traversal rejection. The new platform catalog test loads data only and proves it neither creates directories nor initializes Git. Tests do not authorize real workstation installation, relocation, Zsh/RTK changes or grant expansion.

CI uses a pinned Debian container, checks actual wheel resources outside the checkout, and uploads distributions and coverage even when the coverage gate fails. It does not prove desktop behavior on another OS. Existing untested installer/application branches remain in the report and keep the gate red; no exclusions or destructive system execution are used to obtain a percentage.
