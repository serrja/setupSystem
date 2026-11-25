# ⚙️ Settings and profiles

The shell loads packaged numeric preferences from `config/application.toml` through `setup_system.configuration.Settings.application()`. Explicit `SETUP_SYSTEM_` variables may override the fields in the generated reference. [Application settings](settings_map.md)

Each plugin owns its Pydantic settings model, field labels, constraints and action inputs. The SDK resolves model defaults, the selected profile, saved TOML values, explicitly declared environment overrides and one-time inputs in increasing priority. A plugin declares environment fields under its own `SETUP_PLUGIN_<ID>_` namespace. The worker validates the resulting values and current choices again.

Named profiles are stored under `$XDG_CONFIG_HOME/setup-system/plugins/<id>/<profile>.toml`. A profile contains a schema version and values. Saving compares the previous revision and preserves the replaced file. When the form reports a conflict, reopen the profile and inspect the external change. Keyring references identify secrets held in the system Secret Service.

The packaged base and workspace profiles contain action selections. Each selection identifies a plugin, action and optional named profile. Program defaults, source digests, platform facts, privilege policy and ownership each have a separate source. [Plugin author guide](plugin_authoring.md)
