# Development

Use an isolated normal-user checkout with uv and the Python version selected for the workspace. The host and SDK use separate source-layout packages.

```sh
make prepare
make check
make check-installed
make plugin-types
make plugins-check
```

The host gate runs Ruff, strict mypy, pytest and lockfile validation. The artifact check installs the host and SDK wheels in a temporary environment and runs the CLI migration from outside the checkout. The plugin type check covers every workspace plugin. Each plugin owns its additional CI toolchain and target environments.
