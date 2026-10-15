# Recovery and verification

## 🚀 Restore configuration

After supported Debian, Ubuntu or macOS and Python 3.10+ are available, copy the portable `setupSystem` source into any checkout path. Debian 12's stock Python 3.11 satisfies bootstrap; the CLI uses uv-managed Python 3.14. A fresh Mac needs a trusted Python installation before a Python bootstrap can run; install Python through the supported package source for that host, verify `python3 --version`, then run `python3 bootstrap.py plan`. Review the resources before `python3 bootstrap.py apply --yes`. The generated `.venv` and Codex completion are caches. Managed sources are `assets/`; user-local state is in XDG config, cache and state directories.

The optional terminal profile is `python3 bootstrap.py apply --profile terminalDev --yes`. It installs pinned Neovim, plugins, parsers, editor tools and XDG configuration. System packages that need unavailable privileges are reported as blocked. Run `setup-system doctor --profile terminalDev` afterward and use the [Russian Neovim guide](neovimGuide.md) for daily work. macOS support is implemented for the terminal profile but has not been locally verified on a Mac.

The Happ profile is `python3 bootstrap.py apply --profile happ --yes`. It preserves an installed package; a missing Debian/Ubuntu package is installed only from the pinned official release. The macOS DMG is staged for native installation and verification. Private state, link handoff and native autostart are covered in the [Russian Happ guide](happGuide.md). Linux arm64 has a Happ/base adapter, while `terminalDev` remains blocked until its editor archives and parser toolchain are pinned.

Run `setup-system backup` before a manual change. For a full vault snapshot, add `--include-vault --vault PATH`; a live SQLite database blocks this operation and needs its owner's backup command. To inspect an existing backup, run `setup-system restore --backup ID --dry-run`. `--apply --yes` restores absent files and preserves conflicting current files. To replace a reviewed conflict, add `--replaceCurrent`; the current file receives its own private backup first. The private manifest stores original paths, hashes and modes. Do not publish it.

## 🛠 System packages

`setup-system apps list` distinguishes package evidence from an executable merely present on `PATH`. The current Ubuntu host has an official ChatGPT `.deb`, Docker Engine, Compose and Buildx packages. The CLI does not update or duplicate them. For a fresh host, review the [official ChatGPT Linux guide](https://learn.chatgpt.com/docs/linux/linux-app) and [Docker Ubuntu guide](https://docs.docker.com/engine/install/ubuntu/) before adding system packages. A local ChatGPT `.deb` name or `Maintainer` field alone cannot prove publisher identity; check its final download source and any published signature/checksum before installation. Do not substitute a same-named Snap.

The Ubuntu `zsh` package can be installed explicitly with `setup-system apps install zsh --yes`. A local user-installed Zsh is preserved. Login-shell changes require a separate action after checking `/etc/shells`; this installer never calls `chsh`.

On Debian and Ubuntu the shared APT execution uses explicit distro/version detection and separate package lists. On macOS the terminal adapter checks the native architecture and Homebrew prefix before using Homebrew for terminal packages. It does not install another Homebrew architecture or Rosetta. The Ubuntu-only application catalog action is not reused on Debian or macOS; `apps install` reports unsupported there. Existing Docker contexts and providers are preserved.

## 🔒 Scope

Launch Codex with `setup-system agents launch NAME --mode review` or `--mode project`. `review` and `network` are read-only; `project` can write only the selected project root when the installed permission profile is enforced. No profile grants Docker socket or `sudo`. The network profile uses Codex's proxy allowlist for command network access. Browser, connectors and MCP have separate permissions. Test enforcement with temporary sentinels in a new session; until then, treat the configured profile as unverified.

Keep program installation, dotfiles, agent assets, vault notes, Google Docs and Git sources under their own owners. Opening a Drive folder is navigation, not a vault sync or backup.
