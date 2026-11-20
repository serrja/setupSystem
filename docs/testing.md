# Testing

`make native-check` runs the locked Ruff/format/type, ShellCheck, Ansible lint/syntax,
entrypoint and fixture gates. `make native-ci` adds wheel/sdist and outside-installed
acceptance. [Development](development.md) owns the exact commands and runtime versions.

Tests use synthetic HOME/XDG, temporary Git fixtures, mock package/system executors,
private APT simulations and headless/PTY Textual. They check source checksums/provenance,
caller validation, unsafe archives, screen changes during apply, immutable snapshots,
consent, cancellation, partial failures, recovery, conflicts and removal-negative cases.
The legacy temporary proxy tests are replaced by direct transport and release-executor
checks; native provider/release, Copier and Debian adapter contracts remain covered.

No test applies the system installer on the developer host. Container/native results
are not physical installation evidence. Real boot, LUKS unlock, GPU/Hyprland, panel,
audio, portals, Firefox policies/extensions and ChatGPT/Happ GUI acceptance require an
explicitly selected disposable Debian target. Report unavailable checks honestly.
