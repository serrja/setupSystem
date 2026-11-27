# 🧪 Test environment

The owner has suspended the product scenario suite. This repository maintains pytest infrastructure for future host, SDK and plugin scenarios under a local exception to the baseRules test gate.

## 🚀 Quick start

Run commands from the workspace root. The default scope is the host and SDK. Select a plugin by its directory name.

```sh
make prepare
make infrastructure-check
make prepare PLUGIN=debian
make infrastructure-check PLUGIN=debian
```

`make collect` and `make test` expose pytest's collection and execution results directly. Both accept `PLUGIN=<name>`. An empty collection returns pytest exit code 5. The infrastructure target recognizes that code, prints `test infrastructure ready; no tests` and propagates all errors. An empty scope provides no product coverage.

## ⚙️ Isolation

The root conftest creates private HOME, XDG, runtime and temporary directories below each test's `tmp_path`. xdist allocates a separate base directory per worker. Database and lock paths derived from these directories stay private. The optional `engine` and `sessions` fixtures initialize a synthetic SQLite database for host development.

The workspace includes pytest-asyncio, pytest-subprocess and pytest-xdist. Async functions use the explicit `asyncio` marker and async fixtures use `pytest_asyncio.fixture`. Both loop scopes are function-local. Supported scenario markers are `unit`, `contract`, `integration` and `e2e`. For parallel collection use `uv run --locked pytest -n 2 tests`. [Development checks](development.md)
