# Desktop fonts

The Hyprland package selection installs Debian's JetBrains Mono, Noto, Noto Color Emoji and DejaVu packages together with Fontconfig. The user's managed Fontconfig preference file is `$XDG_CONFIG_HOME/fontconfig/conf.d/60-setup-system-fonts.conf`. The desktop templates use JetBrains Mono for text and Noto Color Emoji as an emoji fallback. [Package selections](../config/base/packages.toml) · [Fontconfig template](../assets/fonts/60-setup-system-fonts.conf)

Run these commands in the desktop user's session to see the chosen face and file.

```sh
fc-match -f '%{family} %{style} %{file}\n' monospace
fc-match -f '%{family} %{file}\n' 'emoji:charset=1f600'
```

Check Latin and Cyrillic text, box drawing, status symbols and emoji at the output's normal scale. Fontconfig chooses the available fallback for each missing glyph. Restart a running application to pick up new font preferences. [Desktop theme](theme.md) · [Recovery](recovery.md)
