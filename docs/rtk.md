# RTK for development

`setupSystem` pins the full `rtk-ai/rtk` edition in `manifests/rtk.toml`. It is a developer tool, not a dependency of Python services. The provider checks the path and version before making changes and preserves an already suitable installation. Upstream publishes archive SHA-256 values; no release-signature verification is established for the pinned version. A checksum confirms agreement with the published byte list, but does not independently authenticate the publisher.

## Install and update

On a supported Debian host with the setupSystem runtime prepared, review `setup-system rtk plan`, then run `setup-system rtk apply --yes`. The provider selects the Linux architecture archive, verifies its published checksum and executable, backs up the previous binary and installs the replacement atomically under the current user's HOME. Use `setup-system rtk apply --update --yes` for an intentional version replacement. RTK setup does not replace the main workstation entrypoint or perform a general Debian package installation.

## Verify and use

Run `setup-system rtk doctor`, `rtk --version` and `rtk gain --help`. An optional Codex integration is the existing `PreToolUse` hook in `~/.codex/hooks.json`, running `rtk hook codex`; verify actual command rewriting with the installed versions. RTK can shorten output. For complete diagnostics, invoke the original command directly, for example `/usr/bin/git diff --check`, and inspect its exit code. Shortened output does not prove a successful review, migration, secret check or CI run.

To disable the integration, back up `~/.codex/hooks.json`, then remove only its RTK hook entry while preserving other hooks. `setup-system rtk restore --backup ID --yes` restores the previous binary only if the installed file has not changed since apply. Local RTK history and settings remain at their existing user paths; the provider does not delete them. Terminal startup launches no network process.
