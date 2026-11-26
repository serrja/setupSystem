# 🧩 Write a plugin

Create `plugins/<id>/pyproject.toml` with a backend entry in `setup_system.plugins`, for example `setup_plugin_example.plugin:Plugin`. Ship the manifest and offline Help with the import package. Specify the navigation category in the manifest, independently of the package directory.

Use `setup_system_sdk.plugin.PluginSettings` for settings and `setup_system_sdk.schemas.common.Inputs` for command parameters. In the package’s `enums.py`, declare its action IDs as a StrEnum. Register the supported action and its input model with the backend. Provide labels, descriptions, defaults, bounds and UI hints on fields. Identify sensitive values through keyring references. Declare the compatible SDK major version and the permissions for each command.

Implement read-only planning and readback in the activated worker. The protocol carries a version, the stable action ID, typed inputs, resource observations and an operation ID. Native mutation requires a fixed Make target, confirmation and the matching active release. Manual and read-only actions declare their mode explicitly. A backend environment runs with the local user’s access to the filesystem. [Extension trust](plugin_trust.md)

A resource identifies its kind, canonical identity, owner relationship, operation, before and after fingerprints, permissions, consumers and supported cleanup. The host records the plan before mutation. Read back each affected resource. For partial completion, mark only demonstrated changes as verified. Use `Result.events` for bounded `ActionEvent` observations with stable message codes and resource counts. The host binds the operation, action, plugin and UTC timestamp before publishing to History. Keep passwords, tokens, private keys and entire requests out of diagnostics. [Recovery](recovery.md)

Most cards use the native schema editors. For a custom trusted panel, declare `setup_system.plugin_ui`, request `ui-code`, and put the component in a separate module inside the plugin package. Import the host SDK's `PluginPanel` and use its `UIContext` to select actions. Backend-only packages belong in the plugin venv. Restart the host to activate a new UI version. [Textual screens](https://textual.textualize.io/guide/screens/)

Regenerate the trusted package metadata and offline references with `uv run --locked python docker/plugin_reference.py --write`. Review manifest changes alongside the action, exercise the worker using isolated state, and run the [development gate](development.md).
