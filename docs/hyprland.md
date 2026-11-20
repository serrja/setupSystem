# Hyprland

Select Hyprland in Textual's Base system section. Signed official Debian 13 backports
must provide the exact compatible versions in `manifests/platforms.toml`. User configuration
is separate from package acquisition and runs under the normal desktop account.

The managed Lua entrypoint includes `setup-system.profile`; personal overrides stay in
`$XDG_CONFIG_HOME/hypr/local.lua`. Existing unrelated entrypoints require a reviewed
include. Outputs use their preferred mode and automatic scale until you preview an explicit
layout in Textual. The launcher starts the workspace IPC bridge and detects the available
batteries and backlights. [Panel compatibility and display preview](theme.md).

Super+Enter opens Foot; Super+D applications; Super+E Thunar; Super+F9 the power menu;
Ctrl+Alt+F3 reaches TTY recovery. The session wrapper configures portals, audio,
clipboard, notifications and lock/idle without starting a root compositor.

The fallback session launcher is `$XDG_DATA_HOME/setup-system/bin/start-hyprland`.
See [theme](theme.md), [login](desktop_login.md) and [recovery](recovery.md).
GUI/GPU/HiDPI, suspend/lock, sound and screen sharing require a disposable target test.
