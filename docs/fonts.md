# Fonts and readable applications

JetBrainsMono Nerd Font Mono 3.4.0 is the primary family for UI, code and icons.
Noto Color Emoji is a missing-glyph fallback, not the ordinary-text font. The
single policy owner is [fonts.toml](../manifests/fonts.toml): the family, sizes,
[official archive](https://github.com/ryanoasis/nerd-fonts/releases/tag/v3.4.0),
archive/file SHA256 and OFL license. No extra similar font family is installed.

The installer renders Fontconfig, foot, Waybar/Wofi, Mako and Hyprlock settings
from that manifest. Assets contain placeholders; do not copy them by hand. Apply
writes one alias file and backs up previous contents; it never downloads fonts
at shell/editor startup. An intact installation is reused. Only the exact previous
setupSystem alias hash can migrate without an ownership receipt; other unknown
edits require review. User files outside these owned paths remain untouched.

## Apply the selected profile

```bash
setup-system workstation plan --profile fonts
setup-system workstation apply --profile fonts --yes
setup-system workstation doctor --profile fonts
```

Noto Color Emoji must already be installed from the current distro. This portable
profile does not install Debian packages on Ubuntu. GNOME changes are opt-in:

```bash
setup-system workstation apply --profile fonts --gnome --yes
gsettings get org.gnome.Terminal.ProfilesList list
setup-system workstation doctor --profile fonts --terminal-profile EXISTING_UUID
setup-system workstation apply --profile fonts --terminal-profile EXISTING_UUID --yes
```

`--gnome` changes only UI/document/monospace/title fonts, with manifest-owned sizes.
The selected existing GNOME Terminal profile keeps its current font size. A profile
using the system font requires separate review; that preference is not toggled.
Only after diagnosing increased **profile cell spacing**, reset that profile:

```bash
setup-system workstation apply --profile fonts --terminal-profile EXISTING_UUID --reset-terminal-cell-spacing --yes
```

This opt-in resets only `cell-width-scale` and `cell-height-scale` to `1.0`; they
change cell spacing, not glyph shape or display DPI. It does not reset window-local
zoom. If both values are already `1.0`, this flag is not a repair for another cause.
GNOME Terminal's [100% / Ctrl+0 action](https://help.gnome.org/gnome-terminal/app-zoom.html)
resets zoom in the selected window; capture the prior zoom before changing it.
Screen scale, locale, TERM, shell, theme and application-specific font overrides
are preserved. Inspect Qt settings if an application chooses its own font; no
competing theme manager is installed.

The [Hyprland profile](hyprland.md) renders the same policy during its own apply,
including clipboard/power launchers. It remains configuration for Hyprland 0.55.2;
rendered files are not proof of installation, login or correct GUI appearance.
The current desktop is never changed by the fonts profile.

## Refresh an already running GNOME Terminal

An observed Ubuntu/GNOME Terminal 3.58 / VTE 0.84 session kept stale font data after
font installation: the existing terminal measured **20 × 30 pixel cells**, while
a fresh VTE using the same Regular 16 descriptor measured **13 × 29**. Both profile
cell scales and display scale were 1.0. Updating GNOME's actual Fontconfig monitor,
then re-selecting the equivalent Regular descriptor, changed the existing terminal
to **13 × 29**, with its original font value restored. This was a cache/selection
problem, not spaces in the text or a broken installed monospace font. Native GUI
screenshot access was unavailable; measured cells do not replace the requested
before/after visual comparison. These numbers describe that session, not every host.

Use this separate opt-in only for an existing profile already using the installed
primary Regular font:

```bash
setup-system workstation apply --profile fonts --terminal-profile EXISTING_UUID --refresh-terminal-font --yes
```

Preflight requires the existing profile, its custom font, `fc-cache`, `gdbus`, the
user font directory and GNOME's real `org.gtk.Settings.FontconfigTimestamp` property.
The operation backs up the font value before any change. It creates and removes
one exclusively named non-font marker in the user font directory, refreshes that
directory's cache, and waits up to ten seconds for the existing GNOME monitor's
timestamp to change. The [upstream monitor](https://download.gnome.org/sources/gnome-settings-daemon/50/gnome-settings-daemon-50.0.tar.xz)
uses real filesystem notifications; the CLI neither writes the read-only timestamp
nor sends a fabricated D-Bus signal.

After acknowledgement, only the selected profile's font is briefly re-selected
with explicit `Regular`, then restored in `finally`, preserving its size. Both
values are read back. A timeout fails without re-selecting the font. The private
`terminal-refresh.json` receipt records the timestamps and completion or failure;
`gui_verified` remains false. No terminal, session, service or display manager is
restarted. Existing user files are never overwritten by the notification marker.

## Diagnose the actual window

Doctor reports every configured GNOME Terminal profile, its default separately,
font/style/file/spacing matches for ordinary text and symbols, scale settings,
terminal environment hints and process names without arguments. A default profile
is not evidence of the active window. An agent sandbox can hide the terminal and
tmux; inherited environment variables and namespace-local process ancestry are
hints, not active-window measurements. Doctor explicitly leaves zoom, pixel cell
geometry and GUI verification unknown.

```bash
setup-system workstation font-probe
fc-match -f '%{family}; %{style}; %{spacing}; %{file}\n' 'JetBrainsMono Nerd Font Mono:style=Regular'
fc-match -f '%{family}; %{file}\n' 'JetBrainsMono Nerd Font Mono:charset=0416'
fc-match -f '%{family}; %{file}\n' 'JetBrainsMono Nerd Font Mono:charset=1f600'
locale
```

The literal probe includes Latin, Cyrillic, numbers, boxes, icons and emoji.
`No spaces` has ten consecutive letters per group; `Spaces` deliberately inserts
spaces. Equal monospaced advances for narrow `i` and wide `W` are expected. Do not
edit messages or source text to hide visual tracking. Fontconfig's `spacing`
property describes the matched face; it is not a measurement of the terminal's
pixel cells. [Fontconfig matching](https://fontconfig.pages.freedesktop.org/fontconfig/fontconfig-user.html)
keeps the primary family ahead of fallback for ordinary text.

Compare this probe in the same real window at the same size: ordinary terminal,
Codex output, tmux if installed, `nvim --clean`, then the normal Neovim profile.
Use Neovim's `:read !setup-system workstation font-probe` to load exactly the same
text. Inspect `:set ambiwidth?` and terminal-cell screenshots; do not set global
`ambiwidth=double`. Terminal Neovim uses the terminal font, with no conflicting
`guifont` setting. A two-cell/ZWJ emoji still depends on the renderer. Record the
actual profile, cell scale, zoom, output scale and before/after screenshots;
headless glyph-width checks cannot establish the visual result.

## Rollback

```bash
setup-system workstation restore BACKUP_ID
setup-system workstation restore BACKUP_ID --apply
```

Each apply prints its private backup ID. Restore previews by default, refuses
later file/settings edits, restores previous values and removes only unchanged
new files. The separate `workstation backup` snapshots existing profile files;
use the general `setup-system restore --backup ID --dry-run` for that snapshot.
