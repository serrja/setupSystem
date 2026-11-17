# Hyprland workstation

```bash
sh install.sh --release vX.Y.Z
```

Use the inspected `install.sh` from the same verified GitHub release as `vX.Y.Z`.
Direct root login adds `--user NAME` for the existing ordinary account. Delivery
prepares the pinned user runtime and opens Textual; choose Install to review and
confirm the complete plan. Installation configures the existing minimal Debian system
and enables graphical login for the next normal boot. It does not start a session
or reboot. See [Debian prerequisites](debian.md) and the [recovery guide](recovery.md).

## Compatibility and package sources

The Hyprland profile supports **Debian 13 / trixie, amd64 and arm64**. General
setupSystem capabilities also cover Debian 12; that does not promise Hyprland support
on Debian 12. The installer checks the actual release, codename and architecture.
Unsupported targets stop before installation. No testing/unstable distribution switch
or source build is used.

[Official trixie-backports](https://backports.debian.org/Instructions/) supplies
[Hyprland 0.55.2](https://packages.debian.org/trixie-backports/hyprland),
[hyprland-guiutils 0.2.2](https://packages.debian.org/trixie-backports/hyprland-guiutils),
hypridle 0.1.8, hyprlock 0.9.6, hyprpolkitagent 0.1.3 and the Hyprland portal 1.4.1.
The explicit guiutils package supplies `hyprland-dialog` and `hyprland-run`; Hyprland
does not pull it in automatically. The graphical login uses Debian
`greetd`, `gtkgreet` and `cage` without a full GNOME/KDE desktop.
Their exact Debian package revisions and the base-package set live in the single
[capability manifest](../manifests/platforms.toml). Availability was checked in the
[amd64](https://deb.debian.org/debian/dists/trixie-backports/main/binary-amd64/Packages.xz)
and [arm64](https://deb.debian.org/debian/dists/trixie-backports/main/binary-arm64/Packages.xz)
indexes. Base packages remain unqualified so official security/updates candidates and
newer installed versions stay eligible; only the compatible backports set is pinned.
Apply additionally verifies the signed source and current dependency resolution.
Missing pins, a failed refresh, an APT lock or any proposed package removal is an error.

The explicit package set also has the hard dependency chains needed by a minimal
system with `--no-install-recommends`: Hyprland → libxkbcommon0 → xkb-data and
Hyprland → libegl1 → libegl-mesa0 → mesa-libgallium; Hyprland directly requires
libgl1-mesa-dri. dbus-user-session requires libpam-systemd and systemd. These are
Debian package `Depends`, not optional recommendations. The profile uses Debian's
default systemd/logind and PAM login; it does not replace another init system.
The [pam_systemd contract](https://manpages.debian.org/trixie/libpam-systemd/pam_systemd.8.en.html)
creates the user runtime directory on a fresh local login. Mesa's presence supplies
the EGL/DRI implementation; it does not establish that a particular GPU or renderer
has passed a real graphical test.

Read-only inspection needs no root and never prepares packages or a runtime:

```bash
setup-system install plan --source /absolute/source/path
```

`sh install.sh --help` is available before Python and only describes delivery.
Full planning uses the delivered CLI; it does not prepare prerequisites. The complete
package simulation uses refreshed metadata only after apply has been accepted.
A preliminary plan is not proof that installation or graphical login will succeed.

## User files and first login

The target is the verified account that invoked sudo. Direct root login must specify
`--user NAME`; UID 0 cannot be a desktop target. HOME comes from that account's passwd
entry. Explicit `--xdg-config-home`, `--xdg-data-home`, `--xdg-state-home` and
`--xdg-cache-home` paths must remain within that HOME and avoid symlinks. The final
installer output shows the actual chosen paths.

At the next normal boot, greetd presents the graphical login on VT7. Authenticate
with the existing ordinary account through Debian's PAM stack; Hyprland starts after
successful authentication. There is no autologin, forced logout, immediate session
switch or automatic reboot. An existing different display manager causes a review
error instead of being replaced.

A local TTY remains the fallback. Log in as the target user and run the printed
entrypoint. With default XDG paths it is:

```bash
"$HOME/.local/share/setup-system/bin/start-hyprland"
```

This managed wrapper restores the chosen XDG directories and invokes Debian's
`/usr/bin/start-hyprland`. It requires a real local user runtime directory, refuses
root, SSH and an already running graphical session, and uses the login session's
D-Bus bus or `dbus-run-session`. It is never sourced by `.bashrc` or Zsh startup.
Use **Super+F9 → Logout** to return to the graphical login, or to the console when
using the TTY fallback. **Ctrl+Alt+F3** switches to a troubleshooting TTY.

Existing disk/HOME encryption is inspected, not created, removed or migrated. Existing
disk-unlock prompts and the account's PAM unlock integration remain in place. Locked
or unverifiable encrypted HOME access must be resolved through the existing login
before installation. An unknown result is not evidence that data is unencrypted.
GRUB remains installed with hidden menu and zero timeout, including recordfail.
Encryption prompts still require their normal unlock; no GRUB recovery-key waiting
window is guaranteed. See [recovery](recovery.md) before changing boot settings.

The managed Lua entrypoint is `$XDG_CONFIG_HOME/hypr/hyprland.lua`; profile assets are
in `hypr/setup-system/`. Existing unknown or edited managed files cause a conflict
before replacement. User files are backed up with hashes and provenance. A successful
repeat apply reuses unchanged files and creates no duplicate backup. System package
changes are not transactionally rolled back.

## Daily controls

| Keys | Action |
| --- | --- |
| Super+Return | Foot terminal |
| Super+E | Thunar file manager |
| Super+D / F1 | Wofi application launcher |
| Super+F2 / F3 | Audio / network settings |
| Super+F4 / F5 / F11 | Close / toggle floating / fullscreen |
| Super+arrows | Move focus |
| Super+Shift+arrows | Move a window |
| Super+Ctrl+arrows | Resize a window |
| Super+1…0 | Workspaces 1…10 |
| Super+Shift+1…0 | Move window to workspace |
| Super+mouse drag | Move / resize with left / right button |
| Super+Space | Switch the default US/Russian layout |
| Super+Escape | Lock |
| Super+Print | Copy a selected screenshot |
| Super+F8 / F9 / F12 | Clipboard history / power menu / key help |
| Media / brightness keys | Sound, playback and available backlight |

Waybar, Mako, Wofi, Foot, Thunar and Swaybg each have one role. Adwaita icons/cursors,
a restrained dark theme and the shared pinned font policy provide the initial style.
The original wallpaper is shipped in this repository. The font archive is verified
and installed by the user stage; [fonts](fonts.md) describes its provenance.

Inside the Hyprland session, the helper imports the actual Wayland environment into
the user manager and starts PipeWire sockets, WirePlumber, panel, notifications,
clipboard and idle/lock helpers. The authentication agent runs as the user. The
Hyprland portal handles screen capture and the GTK portal handles file selection.
NetworkManager's applet appears only when its service is already active; the installer
does not replace network configuration. Blueman's applet runs when a Bluetooth
controller exists. Optional helper failures are logged independently at
`$XDG_STATE_HOME/setup-system/hyprland-session.log` (default: `~/.local/state/`);
a failed audio/user-bus command does not prevent the other helpers from starting.
Power and brightness use the available hardware and logind; no kernel, swap, driver
or login shell tuning is required.

Waybar shows each present system battery; peripheral batteries and absent devices are
excluded. A desktop without a system battery has no battery widget. Backlight controls
appear only for an available kernel device. The clock uses the system's local timezone;
its tooltip includes the date, seconds and timezone. Check the installation plan's
clock observations when the time is wrong. Leaving Textual's timezone field blank,
or omitting `--timezone` on `install plan`/`install apply`, preserves the current zone.
An explicit installed IANA zone such as `Europe/Paris` is validated before changes.
Existing locale values are preserved. Existing time providers are retained; where
inspection conclusively finds no provider, the plan may add `systemd-timesyncd` and
enable synchronization. Unknown provider state does not authorize a replacement.

Debian's Waybar 0.12 workspace clicks use the older Hyprland IPC syntax. The managed
panel therefore uses custom workspace buttons and one user-session bridge to the
pinned compositor's Lua dispatch API. Buttons and scrolling use that adapter; workspace
state comes from the current compositor rather than fixed active labels. Other panel
roles use Waybar's native modules.

Brightness keys and panel scrolling use a small unprivileged helper and logind's
[Session.SetBrightness API](https://manpages.debian.org/trixie/systemd/org.freedesktop.login1.5.en.html).
It discovers an actual `/sys/class/backlight` device, prefers its native/raw interface,
requires a local active session and changes brightness in five-percent steps within
a two-to-one-hundred-percent range. Values go through the authenticated system bus;
the helper never writes sysfs, adds privileged groups, installs udev grants or invokes
sudo. Debian's brightnessctl build does not include logind support, and its optional
udev rules require video/input groups, so that direct-write command is not used.
An absent backlight (common with external monitors), inactive/remote session or driver
permission failure returns an explicit error. Use the monitor's physical controls
where no kernel backlight interface exists. Hardware behavior remains unverified
until the real guest smoke test.

## Personal overrides and fallbacks

Create `$XDG_CONFIG_HOME/hypr/local.lua` for personal settings; it is loaded after the
managed profile and is never replaced. For example:

```lua
-- Use a fixed scale for all automatically discovered monitors.
hl.monitor({ output = "", mode = "preferred", position = "auto", scale = "1" })
hl.config({ input = { kb_layout = "us,ru", kb_options = "grp:win_space_toggle" } })
```

The initial monitor rule uses preferred mode, automatic position and automatic scale;
no connector name, GPU or absolute HOME is embedded. If auto scale is unsuitable,
use `"1"`, `"1.25"` or `"2"` after checking the actual monitor. For a troublesome GPU,
return to a TTY and inspect the compositor log before choosing a hardware-specific
setting. No proprietary driver or renderer override is imposed.

Restore commands preview by default and refuse to replace later edits. With default XDG
paths, use the installed runtime CLI below; for custom paths use the exact command
printed by the installer:

```bash
"$HOME/.local/share/setup-system/runtime/bin/setup-system" workstation restore BACKUP_ID
"$HOME/.local/share/setup-system/runtime/bin/setup-system" workstation restore BACKUP_ID --apply
```

The profile tests cover generated files, no-write planning, conflicts, quoted paths,
repeat apply and restore using fixtures. **REAL INSTALL / GUI SMOKE NOT RUN** unless
a separate acceptance record identifies a disposable guest and the tested source SHA.
Renderer/GPU, actual login, audio, screen sharing, lock/resume and network/TTY retention
must be checked in that guest; package or mock success does not establish them.
