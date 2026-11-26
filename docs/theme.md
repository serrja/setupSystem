# Catppuccin Mocha Green

The package supplies the shared palette, font roles and authentication geometry under `/usr/lib/setup-system/config/shared`. Each desktop extension renders its own configuration.

## ⚙️ Desktop appearance

Set compositor effects and window corners on the **Hyprland** page. Set compact mode, opacity and panel radius under **Waybar**. The compact panel is 30 pixels high at the top of the display. Its battery and backlight controls depend on observed hardware.

Choose terminal font size under **Foot**. **Hyprlock** controls the native lock screen's empty field behavior. **Greeter** uses the shared card and input geometry for new local logins. Each program has a separate Apply action. [Graphical login](desktop_login.md) · [Fonts](fonts.md)

## ⚙️ Review a monitor mode

In an active Hyprland session, select an observed output, mode, position and scale. **Test monitor temporarily** applies the proposed mode for twelve seconds and checks that the original layout returned. **Save monitor layout** writes the reviewed configuration for the session. The backend checks the output's advertised modes again before publishing. [Hyprland](hyprland.md)

Open the extension’s **Help** tab for offline instructions. [Waybar Help](../plugins/waybar/src/setup_plugin_waybar/help.md) · [Hyprlock Help](../plugins/hyprlock/src/setup_plugin_hyprlock/help.md)
