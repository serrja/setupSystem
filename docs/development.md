# 🛠️ Development

Use uv and Python 3.12 or later from the workspace root. Keep build products, user profiles and databases separate from your workstation configuration.

## 🧪 Test environment

```sh
make prepare
make check
make plugin-types
```

The host gate checks formatting, lint, types, the pytest environment and the lockfile. Plugin type checking includes the shared desktop data package. [Pytest scopes and exit codes](testing.md)

## 📦 Build and releases

```sh
make build
make check-installed
uv build --all-packages
make plugins-check
```

The installed check verifies host wheel bytes, distribution metadata and CLI discovery outside the checkout. Package-owned checks run through each plugin's `ci/Makefile`. Debian archive verification needs the distribution's build tools. [Plugin authoring](plugin_authoring.md) · [Release guide](release_guide.md)
