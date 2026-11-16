# setupSystem

```bash
sudo bash ./setup.sh
```

Configure an existing minimal Debian installation as a complete Hyprland workstation.
The installer prepares its dependencies, installs the compatible compositor and daily
applications, and writes backed-up configuration for the real sudo caller.

## 🚀 Requirements and result

The Hyprland target is **Debian 13 / trixie, amd64 or arm64** with working APT/dpkg,
Bash, Debian's archive keyring, network access and a normal account already allowed to
use sudo. Obtain and verify this local source/release bundle first. Python, uv, pip,
Git, Codex, gh and a graphical desktop are not prerequisites.
Python and CA trust readiness are checked separately. After consent, signed Debian
bootstrap installs missing prerequisites and rebuilds an absent CA bundle with
Debian's tool, preserving configured trust; HTTPS downloads require a ready bundle.

The selected profile requires the Debian systemd 257 client and an accessible
systemd 257 system manager. The initial Debian bootstrap, Xray asset and HTTPS
subscription endpoint must be reachable before the temporary proxy exists. If the
Xray release host is blocked, the verified optional source-bundle seed is
`downloads/xray-v26.11.9-amd64.zip` or `downloads/xray-v26.11.9-arm64.zip`, matching
[the pinned network manifest](manifests/network.toml). A seed covers the client
asset; the subscription endpoint still needs initial access.

From another directory, use the absolute path:

```bash
sudo bash "$HOME/Projects/serrja/setupSystem/setup.sh"
```

Review the full plan and confirm once. The installer then requests an HTTPS VPN
subscription using hidden terminal input. A temporary non-root Xray client supplies
a loopback HTTP proxy only while Happ, signed Debian packages, the locked user
runtime and fonts are downloaded and verified. It stops and its temporary config is
removed before offline package apply in its network namespace. Native Happ helper
activation then runs as a separate system operation outside that namespace, preserving
an already running helper. User configuration runs in its own offline network
namespace. Happ's GUI and subscription import happen after graphical login.

For unattended apply, `--yes` also requires an existing private input file:

```bash
sudo bash ./setup.sh --yes --vpn-config-file "$HOME/.config/setup-system/subscription.input"
```

The file must be an absolute path inside the verified user's HOME, owned by that
user, regular, single-link, 1..8192 bytes and inaccessible to group/others (for
example mode 0600). Symlinks and traversal are rejected. It contains one HTTPS
subscription address and is preserved after use. Pass the filename, never the URL,
as an argument. Without a TTY or explicit consent, apply exits 2; without a TTY,
`--yes` without the private file also exits 2 before prerequisite installation.
A direct root login must supply `--user NAME`; sudo selects its verified caller
and passwd HOME automatically. Root installs system packages; the user owns the
runtime, fonts, configuration and backups. Existing network configuration, locale,
timezone, login shell and running sessions are preserved.

The temporary client needs a supported VLESS node from a plain URI/list, a
base64-encoded VLESS list, or compatible Xray JSON, using TLS or REALITY.
An HTTPS address secures retrieval; it does not guarantee a compatible or secure
payload. Encrypted Happ payloads and unsupported provider formats cannot bootstrap
this console stage. Import those through Happ's native GUI after login.

The profile includes Waybar, Wofi, Foot, Thunar, Mako, wallpaper, fonts/theme,
PipeWire/WirePlumber, portals, authentication, clipboard/screenshots, lock/idle and Happ.
The compatible Debian backports revisions and architectures live in
[one manifest](manifests/platforms.toml). Unsupported targets stop before changes;
general portable setupSystem capabilities on Debian 12 do not imply Hyprland support.

## 🔎 Plan, repeat and recover

```bash
bash ./setup.sh plan
bash ./setup.sh --dry-run
```

Both are read-only and need neither root nor downloads. Without Python they show a
preliminary shell plan; package availability/signatures are checked during the
explicitly authorized APT refresh in apply. Explicit `--xdg-config-home`,
`--xdg-data-home`, `--xdg-state-home` and `--xdg-cache-home` accept safe subdirectories
of the verified HOME, including spaces, without `sudo -E`.

Repeat the same installation command after a recoverable failure. Managed files and
sources are idempotent; unknown or locally edited files require review. See
[recovery](docs/recovery.md) for receipts/backups and interrupted APT. Package changes
are not transactionally rolled back.

After installation, log in locally on a text console as your normal user and run:

```bash
"$HOME/.local/share/setup-system/bin/start-hyprland"
```

For a custom data directory use the exact path printed by the installer. The session
starts only on request. Super+F9 opens the logout menu; Ctrl+Alt+F3 switches to a TTY.
See [keys, scaling and session integration](docs/hyprland.md). Open Happ from the launcher
and use its native GUI subscription import; the installer does not populate its private database.

**Evidence level:** automated fixtures and native build/installed-wheel checks.
**REAL INSTALL / GUI SMOKE NOT RUN**: actual login, renderer/GPU, audio,
screen-sharing, lock/resume and network/TTY preservation need a disposable Debian
VM or a separately selected target. Mocks and containers do not establish GUI success.

## 📚 Other tools and development

- [Debian installation](docs/debian.md) · [Linux capabilities](docs/linux.md)
- [Terminal](docs/terminal_guide.md) · [Neovim](docs/neovim_guide.md) · [Fonts](docs/fonts.md)
- [Project templates](docs/templates.md) · [Workspace](docs/workspace_guide.md)
- [Git author profiles](docs/git_profiles.md) · [Happ](docs/happ_guide.md) · [RTK](docs/rtk.md)
- [Contributing](CONTRIBUTING.md) · [Development](docs/development.md) · [Releases](docs/release_guide.md)

Existing projects/templates/apps and terminalDev remain available through the
installed CLI. They are separate explicit tools; the Hyprland installation needs
no additional manual profile command. Delivery follows working branch → test → dev →
main with exact-SHA checks and releases.
