# Graphical login, clock and boot

The Hyprland profile configures the existing Debian installation. It keeps existing
boot entries, encryption and authentication, then enables a graphical login for the
next normal boot. Apply never switches sessions, starts a display manager or reboots.
See [first launch](../README.md#github-delivery-and-first-launch) for delivery and
[Hyprland](hyprland.md) for the managed desktop.

## Graphical authentication

The profile installs Debian's greetd, gtkgreet and Cage. The greeter runs on VT7 as
Debian's `_greetd` account with system-owned styling and fonts. Enter the normal target
account credentials; greetd's existing PAM login stack authenticates the user, and a
small system-owned bridge starts the managed Hyprland launcher as that user. No
password is collected by setupSystem. No autologin or root graphical session exists.
The bridge preserves the verified HOME/XDG paths and accepts only the authenticated
local session. It does not read user shell startup to obtain privileged commands.

The installer manages `/etc/greetd/setup-system.toml` and the explicit unit override
`/etc/systemd/system/greetd.service.d/50-setup-system.conf`. Debian's vendor greetd
configuration and PAM files remain untouched. Per-user records live under
`/etc/setup-system/login-users/`; `/usr/local/libexec/setup-system/login-session` is
the checked post-authentication bridge. Another display manager or an unknown greetd
override stops planning for review instead of being silently replaced.

After successful apply, `graphical.target` is the default boot target and
`display-manager.service` points to greetd. Debian package configuration can enable
its vendor service alias earlier; package startup remains deferred. Do not assume
that a failed later stage reversed package configuration. The current session and
network connection are retained. At the next ordinary boot, authenticate at the
greeter. Super+F9 → Logout returns there. Ctrl+Alt+F3 opens another console; log in as
the desktop user and use the exact fallback launcher printed by setup.

## Existing encryption

Read-only inspection reports root and HOME encryption separately. LUKS/dm-crypt,
eCryptfs and fscrypt evidence is described without collecting keys or passwords.
An unknown result is reported as unknown, rather than unencrypted. The installer does
not create encryption, migrate data or modify crypttab, fstab, initramfs or PAM.

Boot-time disk unlocking still happens before graphical login. The greeter uses
system files so an encrypted user HOME is not needed to draw it. Native PAM login
remains responsible for any existing HOME-unlock integration. A locked or unverifiable
eCryptfs/fscrypt HOME must be unlocked through its existing local login before the
user runtime can be prepared. Existing fscrypt requires its status utility for that
check. The delivery script checks access before creating runtime/download files.
No new encrypted-HOME login compatibility is claimed without a real machine test.

## Local time and hardware

Leave Textual's optional timezone field empty to retain the detected local timezone.
An explicit IANA name is validated before apply; Typer exposes the same selection as
`install plan --timezone AREA/CITY` and `install apply --timezone AREA/CITY`.
Waybar uses system local time; its tooltip includes the timezone and numeric offset.

The plan reports NTP capability and current providers. An installed provider such as
chrony or ntpsec remains in place. The profile adds Debian's systemd-timesyncd only
when inspection positively identifies no installed provider and no available NTP
service. Eligible disabled time synchronization is enabled through timedatectl;
RTC mode and server configuration are retained. Clock adjustments cannot be undone
transactionally.

The panel discovers actual system batteries and an available native/raw backlight.
It excludes peripheral batteries and hides absent hardware. A desktop with no battery
therefore has no invented battery percentage. Workspace clicks use the pinned
Hyprland Lua IPC through the managed panel helper rather than incompatible legacy
Waybar workspace dispatch. Session helper failures are logged privately at
`$XDG_STATE_HOME/setup-system/hyprland-session.log`, independently of other helpers.

## Zero-delay GRUB

GRUB remains installed. The managed `/etc/default/grub.d/99-setup-system.cfg` sets
`GRUB_TIMEOUT_STYLE=hidden`, `GRUB_TIMEOUT=0` and `GRUB_RECORDFAIL_TIMEOUT=0`.
The installer runs the existing update-grub command; it never runs grub-install or
changes kernel arguments, boot entries or cryptodisk settings. An installed GRUB and
its controlled configuration are required; no other bootloader is migrated.

The requested zero-second policy also removes the waiting period after a recorded
boot failure. There is no guaranteed recovery-key waiting window. Keep an existing
recovery medium available. An encryption-unlock prompt remains visible when required;
it is separate from the GRUB menu timeout.

## Recovery

The final `system-desktop` stage starts only after user configuration succeeds. A
failure returns a nonzero result and retains the completed user stages in
`/var/lib/setup-system/install.json`. Fix the reported cause and retry the same
verified entrypoint; later edits are preserved as conflicts for review.

Managed system-file provenance and hashes live in
`/var/lib/setup-system/desktop.json`. Replaced bytes are backed up privately under
`/var/lib/setup-system/desktop-backups/<path-sha256>/<content-sha256>`, with adjacent
JSON recording the original path and mode. Unchanged reruns reuse these backups.
On a handled desktop-stage error, setup attempts to restore touched files, generated
GRUB configuration, boot target, service enablement and clock settings. Recovery
failure is reported explicitly; APT changes and the instant of a clock adjustment
are not transactionally rolled back.

For manual recovery from a TTY, first inspect the reported failed stage and private
backup metadata as root. Compare a backup's path, hash and mode with the current file
before restoring it; do not pipe unreviewed metadata into a root shell. Preserve any
later administrator edits. After a reviewed change to GRUB defaults, regenerate the
existing configuration with update-grub. After changing greetd's unit drop-in, reload
the system manager. A switch to `multi-user.target` as the default boot target can
retain console login for a later boot while graphical login is investigated; do not
isolate targets or stop the running session as an automatic recovery step.

These paths and failure boundaries have automated fixture and headless terminal
coverage. **REAL INSTALL / GUI SMOKE NOT RUN**: actual greetd/PAM login, encrypted-HOME
unlock, GRUB boot, hardware clock, battery, renderer, audio, sharing and lock/resume
still require a selected disposable Debian guest. No installer is applied to the
development host to obtain this evidence.
