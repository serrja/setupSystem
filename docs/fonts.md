# Fonts and readable applications

Use Noto Sans for UI, JetBrainsMono Nerd Font Mono 3.4.0 for code/PUA icons and
Noto Color Emoji as fallback. Noto Serif remains the serif alias. Noto packages
come from the current distro; the optional Nerd Font archive comes from the
[official release](https://www.nerdfonts.com/font-downloads), pinned by archive and
individual-file SHA256 in [fonts.toml](../manifests/fonts.toml). The installer saves
OFL.txt alongside the four font styles. Binaries do not live in this repository.

```bash
setup-system workstation plan --profile fonts
setup-system workstation apply --profile fonts --yes
setup-system workstation doctor --profile fonts
```

Apply installs the digest-checked archive only if its managed files are missing or
changed, and writes one fontconfig alias file. It requires Noto already installed;
it does not install Debian packages on another OS. Repeated application creates no
duplicate config entries or startup downloads. Unknown file edits require review.

GNOME changes are opt-in and preserve unrelated settings:

```bash
setup-system workstation apply --profile fonts --gnome --yes
gsettings get org.gnome.Terminal.ProfilesList list
setup-system workstation apply --profile fonts --terminal-profile EXISTING_UUID --yes
```

`--gnome` changes only UI/document/monospace/title fonts. The terminal option changes
only the selected existing profile's font family and preserves its size. A terminal
using the system font requires separate review instead of silently toggling that
preference. GTK reads the GNOME font settings on GNOME. Qt's fontconfig fallback
uses the same families unless the application overrides them; inspect qt5ct/qt6ct,
KDE settings and per-app choices when present, without installing competing theme
managers. DPI, scaling, locale, shell and terminal cell scaling remain unchanged.

## Diagnosis and rollback

```bash
fc-match sans-serif
fc-match monospace
fc-match emoji
locale
printf 'Кириллица │ ┌─┐ │  │ 😀 │ 👩‍💻\nLong line: 0123456789\tTAB\n'
gsettings get org.gnome.desktop.interface text-scaling-factor
gsettings get org.gnome.desktop.interface scaling-factor
setup-system workstation restore BACKUP_ID
setup-system workstation restore BACKUP_ID --apply
```

Each apply prints its private backup ID. Restore previews by default, checks hashes,
refuses later edits, restores prior settings and removes only unchanged newly
created files. The separate `workstation backup` snapshots existing profile files;
use the general `setup-system restore --backup ID --dry-run` for that snapshot.

Compare the sample in the real terminal outside/inside tmux, `nvim --clean` and the
normal profile. Nerd icons and emoji are different glyph systems. A two-cell emoji
or joined emoji can still depend on the terminal renderer. Headless `strdisplaywidth`
checks do not prove GUI appearance. Neovim `guifont` cannot set a TUI terminal font.
Do not globally override TERM, LC_ALL or ambiwidth to conceal a renderer problem.
Review Zsh's non-printing prompt spans and project tabs/line wrapping separately.
