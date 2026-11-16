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

From another directory, use the absolute path:

```bash
sudo bash "$HOME/Projects/serrja/setupSystem/setup.sh"
```

Review the full plan and confirm once, or explicitly choose `--yes` for unattended
apply. Without a TTY or explicit consent, apply exits 2. A direct root login must
supply `--user NAME`; an ordinary sudo invocation selects its verified caller and
passwd HOME automatically. Root installs system packages; the user owns the runtime,
fonts, configuration and backups. Existing network, locale, timezone, login shell
and running sessions are preserved.

The profile includes Waybar, Wofi, Foot, Thunar, Mako, wallpaper, fonts/theme,
PipeWire/WirePlumber, portals, authentication, clipboard/screenshots and lock/idle.
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
See [keys, scaling and session integration](docs/hyprland.md).

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
