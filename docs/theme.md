# Catppuccin Mocha Green

**Working environment** lets you select the desktop theme, panel density, effects, opacity and corner radius. The release renders native Hyprland, Waybar, Foot, Wofi, Mako and Hyprlock files from the shared palette. The greeter, Plymouth and Neovim use the same palette when their respective features are selected. [Palette](../config/shared/palette.toml) · [Desktop asset list](../config/base/desktop.toml)

Hyprland uses 6-pixel inner gaps, 12-pixel outer gaps, 12-pixel default corners and a 2-pixel green active border. The floating top bar is normally 36 pixels high and 30 in compact mode. The launcher and workspace buttons are on the left. The center shows the clock, with the date available on click. Keyboard layout, audio, network and tray appear on the right. The panel discovers batteries and backlights in sysfs and adds their controls when present.

The session's Waybar helper listens for Hyprland workspace events and translates workspace button clicks to the compositor's Lua dispatcher. On a target with updated native packages, verify workspace switching, hotplug and scale on each output before replacing this integration. [Panel implementation](../assets/hyprland/panel.py)

## Display preview

In a running Hyprland session, choose **Inspect** to refresh the connected outputs. Select an advertised mode, scale and position. **Test for 12 seconds** applies the selected layout temporarily. Its helper checks what the compositor applied, reloads the saved configuration and, if necessary, reapplies the previously observed layout. Review the result before confirming a persistent workspace plan. Unconfigured outputs use their preferred mode and automatic scale. [Hyprland](hyprland.md)
