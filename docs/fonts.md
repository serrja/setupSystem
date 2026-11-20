# Fonts

The font profile installs JetBrainsMono Nerd Font Mono for text, code and icons.
Fontconfig selects Noto Color Emoji for missing glyphs. The release manifest owns
the families, sizes, archive URL, license and SHA-256 for each file.
[Font manifest](../manifests/fonts.toml)

Workspace Tools can inspect the current Fontconfig matches, display the glyph
sample and apply the selected fonts. The emoji fallback must already be installed.
Apply verifies the archive and its members, installs the selected files under
XDG data and refreshes the user font cache. A repeat reuses verified fonts.
The desktop profile renders Foot, Waybar, Wofi, Mako, Hyprlock and GTK from the
same settings. [Desktop profile](hyprland.md)

The sample includes Latin, Cyrillic, punctuation, box drawing, icons and emoji.
To inspect the selected face on the target, run the following in the session
whose rendering you want to check.

```sh
fc-match -f '%{family} %{style} %{file}\n' monospace
fc-match -f '%{family} %{file}\n' 'JetBrainsMono Nerd Font Mono:charset=1f600'
```

Recovery previews each managed file and checks for later edits before restoring.
Restart applications that still display the previous face. [Recovery](recovery.md)
