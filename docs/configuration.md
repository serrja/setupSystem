# Configuration

Each section saves its own preferences in `$XDG_CONFIG_HOME/setup-system`, which defaults to `~/.config/setup-system`.

| Section | User file | Packaged example under `/usr/lib/setup-system/config` |
| --- | --- | --- |
| Base system | `base.toml` | `base/example.toml` |
| Working environment | `workspace.toml` | `workspace/example.toml` |

## 💾 Save and reload

Save validates the selected fields, preserves TOML comments and checks the revision read by the form. Reload reads the saved settings again. If the form has unsaved edits, it asks before replacing them. The migration reads known earlier section settings and retains the old file when saving the new format. Backups of replaced settings remain beside the new file.

**Base system** accepts timezone, locale, keyboard and selected desktop or boot changes. **Working environment** groups application and language choices, theme, outputs, project paths, cleanup and recovery. Inspect refreshes suggestions. Output and mode selections are checked against a connected display. Entering a value, switching sections and saving preferences require no elevation.

Preview shows the section, actions and preconditions. Switching sections preserves a pending preview. Editing a field used by its operation requires a fresh preview before Apply. An operation already running keeps its confirmed settings. The request also carries the original preview so that a changed package resolution, file or output can stop the run.

## Earlier user launcher

If you previously used 0.4.x, check which launcher your shell finds.

```sh
command -v setup-system
```

Use `/usr/bin/setup-system` to open the Debian package when an older launcher takes precedence. The package leaves launchers in your home directory alone. To identify the old managed launcher, compare `sha256sum "$HOME/.local/bin/setup-system"` with the digest in `${XDG_STATE_HOME:-$HOME/.local/state}/setup-system/launcher.sha256`. If both regular files belong to you and the digest matches, you can archive that launcher under an unused name and start a fresh shell. Keep a custom or unrecognized launcher in place.
