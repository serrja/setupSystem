# setupSystem

setupSystem is a normal-user Textual application for configuring independently packaged plugins. Each action can inspect settings, preview a plan and verify its changes. The host starts with an empty catalog.

## Run the host

From a checkout with Python 3.12 or later and uv, run the following commands.

```sh
uv sync --locked --package setup-system
uv run --locked setup-system --version
uv run --locked setup-system
```

Open a plugin to edit settings or preview an action. Apply asks you to confirm the reviewed plan. History shows recorded operations. Select a compatible provider when several active plugins implement a required capability.

See [configuration](docs/configuration.md), [database upgrades](docs/database.md) and [plugin authoring](docs/plugin_authoring.md). Platform installation and recovery are documented by their owners, including the [Debian integration](plugins/debian/docs/install.md).
