# Configuration

Save each section separately under `$XDG_CONFIG_HOME/setup-system`. The default directory is `~/.config/setup-system`. The two files are `base.toml` and `workspace.toml`. The package owns its catalogs under `/usr/lib/setup-system/config`.

Save validates the selected fields, preserves TOML comments and checks the revision read by the form. Reload after an external edit. The migration reads known earlier section settings and retains the old file when saving the new format. Backups of replaced settings remain beside the new file.

**Base system** accepts timezone, locale, keyboard and selected desktop or boot changes. **Working environment** groups application and language choices, theme, outputs, project paths, cleanup and recovery. Inspect refreshes suggestions. Output and mode selections are checked against a connected display. Entering a value, switching sections and saving preferences require no elevation.

Preview obtains the actions and their preconditions. Review the complete proposed change before Apply. The request also carries the original preview so that a changed package resolution, file or output can stop the run.
