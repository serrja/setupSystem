# ⚙️ Settings

Host timing, completion limits and SQLite settings belong to `setup_system.settings.Settings`. The process reads installed defaults and accepts named environment overrides. [Host settings](settings_map.md)

Each extension declares its own Pydantic settings and action inputs. The worker resolves defaults, saved values, declared environment overrides and the values confirmed for this operation. A plugin declares eligible environment fields under its `SETUP_PLUGIN_<ID>_` namespace. Secret fields reference entries in Secret Service.

The current settings for an extension live at `$XDG_CONFIG_HOME/setup-system/v10/plugins/<id>.toml`. Saving checks the revision of the file you opened and retains the displaced revision. If another editor saved changes, reopen the settings and review them before saving. Git identities, Docker contexts, Happ subscriptions and Obsidian vaults belong to their respective programs.

Changing an input or selecting another action requires a new Preview. The snapshot binds the current release, settings revision, inputs, resource observations and intended action. [Using the configurator](textual_workflow.md)
