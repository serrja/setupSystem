# Configuration

Each section saves its own preferences in `$XDG_CONFIG_HOME/setup-system`, which defaults to `~/.config/setup-system`.

| Section | User file | Packaged example under `/usr/lib/setup-system/config` |
| --- | --- | --- |
| Base system | `base.toml` | `base/example.toml` |
| Working environment | `workspace.toml` | `workspace/example.toml` |

## How a selection reaches Preview

The configurator resolves each stage through `setup_system.configuration.Settings`. Missing fields use the stage model defaults. An explicitly selected packaged example can supply initial values. Values from the saved stage file override the profile, and confirmed operation values override the saved fields. Nested theme fields merge by name. A list of applications, languages or monitors replaces the earlier list as a whole. Startup opens the saved stages with their normal defaults.

The installed resources under `/usr/lib/setup-system/config` supply the same catalogs and templates used by source and wheel installations. The application reads them relative to its own installation. The current directory, a local `.env` and shell variables for Make and Ansible cannot select a configuration source.

For the normal-user interface, the documented numeric `SETUP_SYSTEM_` variables override `application.toml`. For example, start `SETUP_SYSTEM_SUGGESTION_RESULTS=24 setup-system` to limit directory completions for that session. The offline reference lists the fields, units, defaults and ranges. [Settings reference](settings_map.md)

Operation history and recovery receipts live in state storage. Downloaded template repositories live in the cache. The package owns the palette, login and lock styling, reviewed package sources, resource layout and fixed operations. At Apply, the installed executor validates the request and checks current system facts against the preview.

## 💾 Save and reload

Save validates the selected fields, preserves TOML comments and checks the revision read by the form. Reload reads the saved settings again. If the form has unsaved edits, it asks before replacing them. The migration reads known earlier section settings and retains the old file when saving the new format. Backups of replaced settings remain beside the new file.

**Base system** accepts timezone, locale, keyboard and selected desktop or boot changes. **Working environment** groups application and language choices, theme, outputs, project paths, cleanup and recovery. Inspect refreshes suggestions. Output and mode selections are checked against a connected display. Entering a value, switching sections and saving preferences require no elevation.

Preview shows the section, actions and preconditions. Switching sections preserves a pending preview. Editing a field used by its operation requires a fresh preview before Apply. An operation already running keeps its confirmed settings. The request also carries the original preview so that a changed package resolution, file or output can stop the run.

## Packaged resources

| Source relative to the package | Contents and consumers |
| --- | --- |
| `config/base/` | APT sources, approved package groups, prerequisite probes and Hyprland resources for the base planner and installed executor. |
| `config/workspace/` | Applications, language profiles, editor, project catalog, user commands and separate removal selections. Textual and the native plays use the catalog IDs. A confirmed operation records the resolved source and version. |
| `config/shared/` | The palette, typography and authentication geometry used by both native desktop renderers. |
| `setup_system/layout.py` and Debian metadata | Installed executable trees, guides and package identity checked against dpkg. |
| Request and receipt models | Fixed operations, allowed commands, message limits and schema versions. The executor checks these at its own boundary. |

For an application or language selection, a catalog identifier goes in the workspace file. For a preference such as panel density or opacity, use the workspace theme table. **Preview** shows the resulting changes before confirmation.

## Earlier user launcher

If you previously used 0.4.x, check which launcher your shell finds.

```sh
command -v setup-system
```

Use `/usr/bin/setup-system` to open the Debian package when an older launcher takes precedence. The package leaves launchers in your home directory alone. To identify the old managed launcher, compare `sha256sum "$HOME/.local/bin/setup-system"` with the digest in `${XDG_STATE_HOME:-$HOME/.local/state}/setup-system/launcher.sha256`. If both regular files belong to you and the digest matches, you can archive that launcher under an unused name and start a fresh shell. Keep a custom or unrecognized launcher in place.
