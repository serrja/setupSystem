# setupSystem

```bash
setup-system
```

A Textual terminal application for configuring an existing Debian installation with
Hyprland. Opening the application makes no changes. Inspect the plan, choose Install,
confirm the complete plan and enter the VPN subscription in the hidden input dialog.
Explicit Typer commands remain available for automation and recovery.

## GitHub delivery and first launch

Use an explicit stable release tag from the verified GitHub release. Download and
inspect its `install.sh` from that same tag, then run it in a terminal:

```bash
sh install.sh --release vX.Y.Z
```

Replace `vX.Y.Z` with that release tag. Direct root login requires `--user NAME`.
The small delivery script requires Debian, ready CA trust, APT, tar and core utilities.
It uses curl when available or APT’s native HTTPS downloader without system changes.
It downloads pinned uv only when the matching managed tool is absent, prepares the
pinned Python and locked CLI **as the normal user**, verifies release checksums,
and opens Textual. It contains no workstation planner or APT package installer.
Initial access to GitHub, the runtime and subscription hosts is required.

Use the keyboard throughout: Enter in the target-user field runs the read-only plan.
Tab/Shift+Tab selects buttons or the plan log; Enter activates the focused button.
Choose Install to review the complete plan, then explicitly confirm with Enter.
The VPN dialog focuses its hidden input; Enter submits it and Esc cancels.
After each operation, focus returns to Read-only plan.

The source/runtime stay under the target user's data directory for repeat/recovery.
A changed existing release is preserved for review. Never run uv sync or Python build
hooks as root. The CLI can also be prepared directly from a verified checkout as the
normal user with `uv sync --locked`, then launched with:

```bash
sudo -- "$PWD/.venv/bin/python" -I -B -m setup_system --source "$PWD"
```

An installed CLI accepts `--source /absolute/source/path`. Direct root login uses the
prepared executable with `--user NAME`. Cwd never selects the target HOME. Non-root
launch can inspect a plan; system apply requires root privileges. The obsolete
combined shell installer has been removed; runtime delivery and system setup are
separate responsibilities.

The Hyprland profile requires **Debian 13 / trixie, amd64 or arm64**, working APT/dpkg,
Debian's archive keyring, CA trust, systemd/logind 257, an accessible system manager,
network access and a normal desktop account. General portable Debian 12 capabilities
do not imply Hyprland support. No ISO, partitioning, desktop removal or reboot is involved.

## Installation and automation

Textual and Typer share one installation service: platform/account/path checks, consent,
verified downloads, temporary-network cleanup, offline package apply and offline user
configuration. Root installs system packages; the verified sudo caller owns runtime,
configuration, fonts and backups. Direct root login requires an explicit user. Existing
network connections, locale, timezone, shell and sessions are preserved.

```bash
setup-system install plan --source /absolute/source/path
sudo -- "$PWD/.venv/bin/python" -I -B -m setup_system install apply --source "$PWD"
```

Plan/help are read-only: no downloads, cache creation, APT refresh or prerequisite
installation. Without a TTY, apply requires `--yes` and `--vpn-config-file PATH`.
The private input must be an absolute regular, single-link file under the verified
user's HOME, owned by that user, inaccessible to others and 1..8192 bytes. It contains
one HTTPS subscription URL. Pass the filename, never a secret URL, as an argument.
Explicit XDG overrides are available on `install plan`/`install apply` and must stay
inside verified HOME. Interactive target selection never substitutes an arbitrary UID.

A temporary pinned Xray client supplies a loopback HTTP proxy while Happ, signed Debian
packages, the locked user runtime and fonts download. It stops and removes its temporary
configuration before offline apply. Native Happ helper activation is separate and preserves
an already running helper; the final subscription import uses Happ's GUI after login.
Initial Xray/subscription access is required. The [network manifest](manifests/network.toml)
provides reviewed source-bundle seed names and hashes when the initial client host is blocked.
Supported inputs contain TLS/REALITY VLESS nodes as plain/base64 lists or compatible Xray JSON;
an HTTPS URL alone does not establish format compatibility. Encrypted Happ payloads are unsupported.

The [profile manifest](manifests/platforms.toml) owns compatible package revisions and
architectures. The profile includes Waybar, Wofi, Foot, Thunar, Mako, wallpaper,
fonts/theme, PipeWire/WirePlumber, portals, authentication, clipboard/screenshots and lock/idle.
Package version inspection uses Debian metadata; it does not execute Hyprland outside a session.

## Repeat, recover and log in

Repeat the same apply after fixing a reported failure. Managed sources/files are
idempotent; unknown edits require review. Diagnostics and receipts report actual completed
stages, with a nonzero result for failure. APT changes are not transactionally rolled back.
See [recovery](docs/recovery.md) for backup/restore and interrupted package operations.

After success, log in locally on a TTY as the target user and run the exact printed
session command. The default is:

```bash
"$HOME/.local/share/setup-system/bin/start-hyprland"
```

No compositor starts as root or automatically during package installation. Super+F9
opens logout; Ctrl+Alt+F3 switches to TTY. See [Hyprland](docs/hyprland.md) for keys,
scaling, audio, portals and session integration. Import the subscription through Happ's
native GUI after login; its private database is preserved.

Evidence: automated fixtures, headless Textual interaction, native build/installed-wheel
checks and isolated APT component tests. **REAL INSTALL / GUI SMOKE NOT RUN**: login,
GPU/renderer, audio, screen-sharing, lock/resume and network/TTY preservation require a
separately selected disposable Debian guest. Containers do not establish GUI success.

## Tools and development

- [Debian](docs/debian.md) · [Linux architecture](docs/linux.md) · [Development](docs/development.md)
- [Terminal](docs/terminal_guide.md) · [Neovim](docs/neovim_guide.md) · [Fonts](docs/fonts.md)
- [Projects](docs/workspace_guide.md) · [Templates](docs/templates.md) · [Git](docs/git_profiles.md)
- [Happ](docs/happ_guide.md) · [RTK](docs/rtk.md) · [Releases](docs/release_guide.md)

Projects/templates/apps, terminalDev, projectRules and backup/recovery remain explicit
tools. Checkout discovery uses only canonical catalog paths; no migration or obsolete
path aliases are supported. Delivery preserves working branch → test → dev → main ancestry.
