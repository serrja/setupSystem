# Hyprland

Select **Hyprland, sound, portals and desktop dependencies** in **Base system**. Plan resolves the Debian packages and prepares the session files for the normal desktop account. The portal configuration prefers the Hyprland backend and routes the file chooser to GTK. The session wrapper requests PipeWire, PipeWire Pulse and WirePlumber from the user service manager and starts its clipboard, notification, lock and panel helpers. The portal preferences are in `/usr/lib/setup-system/ansible/templates/hyprland-portals.conf`. [Debian target](debian.md)

The managed `$XDG_CONFIG_HOME/hypr/hyprland.lua` loads `setup-system.profile` and then reads `$XDG_CONFIG_HOME/hypr/local.lua` when present. Put personal overrides in `local.lua`. Review any reported ownership conflict with an existing entrypoint. The normal-user launcher is `$XDG_DATA_HOME/setup-system/bin/start-hyprland`. Session diagnostics go to `$XDG_STATE_HOME/setup-system/hyprland-session.log`.

## ⌨️ Session shortcuts

| Shortcut | Action |
| --- | --- |
| Super+Enter | Open Foot |
| Super+D | Open the application launcher |
| Super+E | Open Thunar |
| Super+Escape | Lock the session |
| Super+F9 | Open the power menu |
| Ctrl+Alt+F3 | Switch to a text console |

Check sound, screen sharing through the portal, clipboard, monitor scaling, lock and suspend on the actual target hardware. Read the desktop shortcuts in `/usr/lib/setup-system/assets/hyprland/keys.txt`. [Display preview](theme.md) · [Graphical login](desktop_login.md)
