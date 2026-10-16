# Contributing to setupSystem

Start a short `type/shortCamelCaseIntent` branch from the actual default branch. Keep platform changes separate from dotfile styling, review the complete diff and outgoing commits, and use a `type(scope): intent` PR title.

## 🧪 Local checks

```bash
uv lock --check
uv sync --locked --python 3.14
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest -q
python3 bootstrap.py plan
```

On a non-Debian host, `bootstrap.py plan` must return exit 4 with `UNSUPPORTED`; that is an expected platform gate, not a Debian installation pass. Check code and docs in the changed area before review. A release, system apply, package publication, and production deployment require separate authorization.
