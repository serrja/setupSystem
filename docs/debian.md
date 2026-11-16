# Debian installation

Prepare the locked CLI as the normal user, then launch the Textual application:

```bash
uv sync --locked
sudo -- "$PWD/.venv/bin/python" -I -B -m setup_system --source "$PWD"
```

Alternatively, use the small GitHub delivery script from an explicit verified release:
`sh install.sh --release vX.Y.Z` (add `--user NAME` from a direct root login). It requires
ready CA trust, APT, tar and core utilities; it downloads pinned uv/Python and prepares
the locked CLI as the normal user before opening Textual. It does not install system
packages. Use the source/script and checksum files from the same versioned release.
Initial access to GitHub/runtime hosts is required; the VPN cannot download itself.

For direct checkout preparation, Python 3.12+ and uv are prerequisites. Do not run
Python build hooks as root. System installation always requires APT/dpkg, Debian's
archive keyring, ready CA trust and a verified local source bundle.

The Hyprland profile supports Debian 13/trixie on amd64/arm64 with systemd/logind 257.
Detection uses the target OS/version/codename and architecture. It rejects unsupported
systems and unsafe target accounts/paths before writes. The profile does not create
images, partition disks, reinstall Debian, remove desktop packages or reboot.

## Inspect and apply

Opening Textual is read-only. Choose Read-only plan, then Install; the full current
plan is displayed before confirmation. The hidden input dialog requests the HTTPS VPN
subscription. An explicit command uses the same service:

```bash
setup-system install plan --source /absolute/source/path
sudo -- "$PWD/.venv/bin/python" -I -B -m setup_system install apply --source "$PWD"
```

Plan/help do not refresh APT, download files or create caches. Noninteractive apply
requires `--yes` and an absolute `--vpn-config-file PATH`. The regular private file
must be user-owned, single-link, inside verified HOME, 1..8192 bytes and inaccessible
to others. It contains one HTTPS subscription URL and is preserved. Secret URLs are
never shell arguments. Explicit XDG overrides remain inside verified HOME.

The sudo caller's UID/GID/name are checked against passwd/group. Direct root login
requires an explicit non-root user; there is no first-user or UID-1000 fallback.
User processes regain the correct groups and HOME, with a closed environment.

## Packages and networking

The managed official trixie-backports source does not rewrite existing sources.
Signed metadata, exact compatible package selections, removal/downgrade guards and
APT `--no-remove` remain mandatory. There is no blanket distribution upgrade or purge.

Temporary Xray runs as the target user with a loopback HTTP listener. Happ, signed
APT packages, runtime dependencies and fonts download and verify before cleanup.
Initial client/subscription access is required; the pinned network manifest describes
an optional matching source-bundle seed. Supported subscriptions contain TLS/REALITY
VLESS nodes in plain/base64 or compatible JSON; encrypted Happ payloads are unsupported.

The temporary client/config is removed before offline APT and user configuration.
Native Happ helper activation is separate and preserves an already running helper.
The final subscription is imported through Happ's GUI after login. Existing network,
locale, timezone, shell and sessions are preserved.

## Login and recovery

Log in locally as the target user and run the exact printed session command, normally
`"$HOME/.local/share/setup-system/bin/start-hyprland"`. No GUI starts during installation.
Repeat the same apply after fixing the reported cause. Unknown configuration edits
require review. A failed stage returns nonzero and does not promise transactional
APT rollback. See [recovery](recovery.md) and [Hyprland](hyprland.md).

Automated and container evidence does not verify a real graphical session. A separately
selected disposable Debian guest must test actual installation, repeat apply, login,
renderer, audio, sharing, lock/resume and TTY/network preservation.
