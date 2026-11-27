# 🧩 Greeter

Greeter is a workstation extension for setupSystem. [Actions and recovery](src/setup_plugin_greeter/help.md) · [Settings and inputs](src/setup_plugin_greeter/settings_reference.md)


## 🎨 Desktop theme contract

The `setup-system-desktop-theme` distribution supplies the version 1 palette and geometry used by the greeter, lock screen, editor, shell, panel and terminal. Consumers declare the supported version in their wheel metadata. The library reads packaged TOML locally in the selected plugin environment.

Use `setup_system_desktop_theme.palette.palette()` for a mapping of color names to CSS hex values and `desktop_style()` for validated fonts, panel sizes and authentication screen geometry. Passing a directory reads its explicit TOML files for compatibility. [Palette data](theme/src/setup_system_desktop_theme/palette.toml) · [Style data](theme/src/setup_system_desktop_theme/desktop.toml)
