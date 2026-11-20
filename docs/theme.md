# Catppuccin Mocha Green

`src/setup_system/resources/themes/catppuccin_mocha_green.toml` is the palette owner.
Managed templates share base `#1e1e2e`, mantle `#181825`, crust `#11111b`, surface0
`#313244`, text `#cdd6f4`, subtext0 `#a6adc8`, green `#a6e3a1`, teal `#94e2d5`,
red `#f38ba8` and yellow `#f9e2af`.

The default uses 6px inner/12px outer gaps, 12px rounding, a 2px green active border,
soft shadows and disabled effects. Hyprland selects preferred monitor modes and automatic
scale. The floating top Waybar is 36px (30px compact), launcher/workspaces left,
clock center, language/network/audio/tray right. Existing Lua IPC remains the workspace
adapter; hardware detection suppresses absent battery/backlight modules.

Workspace contains a theme preview, compactness, opacity, rounding and effects. Applying
these changes writes managed configuration without reinstalling system packages. Existing
personal edits require review; unrelated GTK applications receive no injected CSS.

Foot, Wofi, Mako, wallpaper, greetd and Hyprlock use the same palette. GTK uses supported
dark preferences. Neovim has a native offline palette and pinned existing plugins.
The actual monitor/HiDPI, panel and compositor result needs target-machine GUI validation.
