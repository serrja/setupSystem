# 🧩 Plugin authoring

Give the plugin its own distribution in `plugins/<name>`. Depend on `setup-system-sdk` and declare the backend under `setup_system.plugins`. Custom Textual panels use `setup_system.plugin_ui` and the SDK's `ui` extra. Package the manifest, Help, reference and other resources alongside the import package. Use `importlib.resources` to locate them.

Declare actions and capabilities in the manifest. Implement application planning, step execution, readback and final verification using the SDK. A provider supplies a request model and a handler for its versioned capability. A consumer sends a capability request through its worker context and includes the returned step in its plan. Keep step keys stable and assign every mutable resource to exactly one step.

Build a distribution with `uv build --package <distribution>`. Changes to plugin checks belong in its own `ci/Makefile` under the target `check`. The common runner discovers these files by directory.

Declare the interface labels, action modes, permissions and capability requirements under `[tool.setup-system.manifest]` in the plugin's `pyproject.toml`. Register its action inputs, settings and provider models on the backend.

## 🔄 Worker and provider calls

A request identifies the operation and the selected executor. The provider broker checks the action's declared capability and contract version. Inspect returns facts. Plan returns a step bound to the provider generation, handler and resources. The host compares these identities again before execution. The worker dispatches an action through its command registry and returns its receipt. A later readback reports verified observations.

Linux worker supervision requires procfs and child-subreaper support. A read-only query has a deadline. Native terminal dispatch requires a grant for the confirmed step. Privileged execution uses a protected launcher and its own transaction boundary. Declare app-specific session variables in the manifest. Keep private configuration values in their owning plugin.

## 📦 Build and releases

Use the selected package environment for generation. For example, regenerate the Codex manifest and Help reference as follows.

```sh
uv run --locked --package setup-system-plugin-codex python docker/plugin_reference.py --write plugins/codex/pyproject.toml
uv run --locked --all-packages python docker/plugin_reference.py
uv run --locked --package setup-system python docker/settings_reference.py
```

See the [test environment](testing.md) for package-scoped checks.
