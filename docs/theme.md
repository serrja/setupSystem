# Catppuccin Mocha Green

The palette lives in `src/setup_system/resources/themes/catppuccin_mocha_green.toml`.
Desktop templates render the selected opacity, corner radius, optional effects and font
policy into native Hyprland, Waybar, Foot, Wofi, Mako and Hyprlock settings. The same
colors are used for the wallpaper, greetd and Neovim.

The default has 6px inner and 12px outer gaps, 12px corners and a 2px green active
border. The floating top bar is 36px tall, or 30px in compact mode. It shows workspaces
and the launcher on the left, time in the center, and language, network, audio and tray
on the right. The panel discovers batteries and backlights through sysfs. Its workspace
buttons translate Waybar custom-module clicks into the pinned Hyprland Lua dispatch
syntax. Debian 13 ships Waybar 0.12.0, whose native workspace button still sends
`dispatch workspace <id>`. The pinned Hyprland Lua parser expects a dispatcher
expression. Recheck the native button on a disposable graphical session when either
package changes. [Waybar workspace button](https://github.com/Alexays/Waybar/blob/8490a1d9b9a5c8770dd63e9faca0a5e01acedcae/src/modules/hyprland/workspace.cpp#L60-L70)
and [Hyprland dispatch implementation](https://github.com/hyprwm/Hyprland/blob/39d7e209c79d451efab1b21151d5938289da838d/src/debug/HyprCtl.cpp#L1102-L1118).

Use the display form in a running Hyprland session to select an output, an advertised
mode, scale and position. Preview applies temporary compositor rules. After twelve seconds
its helper reloads the saved compositor configuration, including when the application
closes during the preview. Accept the previewed settings for the next confirmed plan.
Other outputs continue to use their preferred mode and automatic scale.

Color and typography changes apply to the named managed applications. Inspect the
rendered result on the intended GPU, outputs and scale before relying on a new desktop.
