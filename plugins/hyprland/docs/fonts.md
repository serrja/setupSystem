# Desktop fonts

The Hyprland configuration action generates `$XDG_CONFIG_HOME/fontconfig/conf.d/60-setup-system-fonts.conf`. Its signed Debian package selection includes JetBrains Mono for text and Noto Color Emoji for missing emoji glyphs. Each desktop renderer reads the shared font roles. Choose a terminal font size on the **Foot** page.

## 🔤 Check the selected fonts

In the desktop user's session, ask Fontconfig which file it selected for each face.

```sh
fc-match -f '%{family} %{style} %{file}\n' monospace
fc-match -f '%{family} %{file}\n' 'emoji:charset=1f600'
```

Check Latin and Cyrillic text, box drawing, status symbols and emoji at the output's normal scale. Fontconfig chooses the available fallback for each missing glyph. Restart a running application to pick up new font preferences. [Desktop theme](theme.md) · [Recovery](../../../docs/recovery.md)

Open the extension’s **Help** tab for offline instructions. [Hyprland Help](../src/setup_plugin_hyprland/help.md) · [Foot Help](../../foot/src/setup_plugin_foot/help.md)
