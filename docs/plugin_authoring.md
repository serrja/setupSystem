# Plugin authoring

Give the plugin its own distribution in `plugins/<name>`. Depend on `setup-system-sdk` and declare the backend under `setup_system.plugins`. Custom Textual panels use `setup_system.plugin_ui` and the SDK's `ui` extra. Package the manifest, Help, reference and other resources alongside the import package. Use `importlib.resources` to locate them.

Declare actions and capabilities in the manifest. Implement application planning, step execution, readback and final verification using the SDK. A provider supplies a request model and a handler for its versioned capability. A consumer sends a capability request through its worker context and includes the returned step in its plan. Keep step keys stable and assign every mutable resource to exactly one step.

Build a distribution with `uv build --package <distribution>`. Changes to plugin checks belong in its own `ci/Makefile` under the target `check`. The common runner discovers these files by directory.

Declare the interface labels, action modes, permissions and capability requirements under `[tool.setup-system.manifest]` in the plugin's `pyproject.toml`. Register its action inputs, settings and provider models on the backend. Regenerate the packaged manifest and reference from the workspace root.

```sh
uv run --locked python docker/plugin_reference.py --write plugins/<name>/pyproject.toml
```
