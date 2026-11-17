# Debian installation

Use the inspected delivery script from an explicit verified GitHub release:

```bash
sh install.sh --release vX.Y.Z
```

Replace `vX.Y.Z` with that release tag; direct root login adds `--user NAME` for the
existing ordinary account. The script requires Debian, ready CA trust, APT, tar and
core utilities. It downloads pinned uv/Python and prepares the locked CLI as the
normal user before opening Textual. It does not install system packages. Source,
script and checksum files must belong to the same versioned release. Initial access
to GitHub/runtime hosts is required; the VPN cannot download itself.

A development checkout has a separate runtime preparation procedure in
[development](development.md); it is not a prerequisite for release installation.
Never run Python build hooks as root. System installation requires APT/dpkg, Debian's
archive keyring and the verified local source bundle retained by delivery.

The Hyprland profile supports Debian 13/trixie on amd64/arm64 with systemd/logind 257.
Detection uses the target OS/version/codename and architecture. It rejects unsupported
systems and unsafe target accounts/paths before writes. The profile does not create
images, partition disks, reinstall Debian, remove desktop packages or reboot.

## Inspect and apply

Opening Textual is read-only. Choose Read-only plan, then Install; the full current
plan is displayed before confirmation. The hidden input dialog requests the HTTPS VPN
subscription. An installed CLI can inspect the same plan:

```bash
setup-system install plan --source /absolute/source/path
```

The explicit `install apply` command uses the same service and requires root privileges;
use the prepared executable and source paths printed by delivery. Direct root invocation
requires `--user NAME`. The timezone field is optional: blank preserves the current zone.
The same choice is available as `--timezone Region/City` on `install plan`/`install apply`.
Only an installed, validated IANA timezone is accepted.

Plan/help do not refresh APT, download files or create caches. Delivery help works
without Python; full planning uses the delivered CLI. Noninteractive apply requires
`--yes` and an absolute `--vpn-config-file PATH`. The regular private file must be
user-owned, single-link, inside verified HOME, 1..8192 bytes and inaccessible to others.
It contains one HTTPS subscription URL and is preserved. Secret URLs are never shell
arguments. Explicit XDG overrides remain inside verified HOME.

The sudo caller's UID/GID/name are checked against passwd/group. Direct root login
requires an explicit non-root user; there is no first-user or UID-1000 fallback.
User processes regain the correct groups and HOME, with a closed environment.

## Packages and networking

The managed official trixie-backports source does not rewrite existing sources.
Signed metadata, exact compatible package selections, removal/downgrade guards and
APT `--no-remove` remain mandatory. There is no blanket distribution upgrade or purge.
The profile explicitly includes `hyprland-guiutils=0.2.2-1~bpo13+1`, plus the compatible
Hyprland/lock/idle/portal set in the [manifest](../manifests/platforms.toml).
Debian's `greetd`, `gtkgreet` and `cage` provide graphical login.

Temporary Xray runs as the target user with a loopback HTTP listener. Happ, signed
APT packages, runtime dependencies and fonts download and verify before cleanup.
Initial client/subscription access is required; the pinned network manifest describes
an optional matching source-bundle seed. Supported subscriptions contain TLS/REALITY
VLESS nodes in plain/base64 or compatible JSON; encrypted Happ payloads are unsupported.

The temporary client/config is removed before offline APT and user configuration.
Native Happ helper activation is separate and preserves an already running helper.
The final subscription is imported through Happ's GUI after login. Existing network,
locale, shell and active sessions are preserved. Timezone is preserved unless explicitly
selected. Clock inspection reports timezone and synchronization; existing time providers
are retained. A default provider is added only when the checked plan establishes that
none is installed. No replacement is inferred from an unknown provider state.

## Login and recovery

At the next normal boot, authenticate on the graphical login screen as the target
ordinary user. Debian PAM opens the user session, then Hyprland starts automatically.
There is no autologin, forced logout, immediate session start or automatic reboot.
A different existing display manager requires review and is not replaced.

Existing disk and HOME encryption are inspected only; no encryption is created,
removed or migrated. Keep the existing disk-unlock and PAM HOME-unlock procedure.
A locked or unverifiable encrypted HOME must be unlocked and checked before setup.
Unknown encryption state is reported as unknown, not as unencrypted.
GRUB remains installed, with its menu hidden and timeout zero, including recordfail;
existing unlock prompts remain. No recovery-key waiting window is guaranteed.

For TTY fallback, log in locally as the target user and run the exact printed command,
normally `"$HOME/.local/share/setup-system/bin/start-hyprland"`. No GUI starts during
installation. Super+F9 opens the session menu; Ctrl+Alt+F3 opens a TTY.
Repeat the same apply after fixing the reported cause. Unknown configuration edits
require review. A failed stage returns nonzero and does not promise transactional
APT rollback. See [recovery](recovery.md) and [Hyprland](hyprland.md).

Automated and container evidence does not verify a real graphical session. A separately
selected disposable Debian guest must test actual installation, repeat apply, login,
renderer, audio, sharing, lock/resume and TTY/network preservation.
