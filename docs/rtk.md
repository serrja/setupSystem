# RTK developer tool

`setupSystem` pins the full `rtk-ai/rtk` release in `manifests/rtk.toml`. This is a developer command, not a Python service dependency. The provider detects the binary and version before making any change; an already matching installation is preserved. The upstream release publishes SHA-256 checksums for the archives, but no release signature was established for this pin. A checksum confirms downloaded bytes against the release checksum file, not publisher identity by itself.

## Install and update

On a new supported Debian workstation, inspect `setup-system rtk plan`, then run `setup-system rtk apply --yes`. The provider selects the pinned Linux architecture asset, checks its published checksum and executable identity, backs up a prior binary, and installs atomically under the current user's home. To replace an older suitable binary deliberately, use `setup-system rtk apply --update --yes`. General Debian package setup remains a separate command.

## Verify and use

Run `setup-system rtk doctor`, `rtk --version`, and `rtk gain --help`. Codex integration is an optional existing `PreToolUse` hook at `~/.codex/hooks.json` running `rtk hook codex`; check its actual rewrite with the installed versions. `rtk` can condense command output. For complete diagnostics, use the original executable directly, for example `/usr/bin/git diff --check`, and inspect its exit code. Do not use condensed output as proof that review, migration, secret, or CI checks passed.

To disable integration, remove only the RTK hook entry from `~/.codex/hooks.json` after backing up that file; keep other hooks. `setup-system rtk restore --backup ID --yes` restores the prior binary only if the installed bytes are still unchanged. Local RTK history and configuration stay under their existing user-owned paths; the provider does not erase them. No network process is started by the shell profile.
