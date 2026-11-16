# Third-party notices

Original repository source and documentation use [MIT](LICENSE).

Configuration snippets are original source. Neovim plugins, system packages,
Zsh, fonts and upstream programs selected by manifests are fetched from their
upstream distributions and retain their own licenses. No fonts or external plugin
source is vendored here. This license does not cover user configuration/secrets.


## Optional workstation fonts and desktop components

Nerd Fonts 3.4.0 JetBrainsMono is downloaded explicitly from the official release,
with pinned hashes and its OFL.txt preserved beside the installed fonts. Noto Sans
and Noto Color Emoji use the distro packages and their installed copyright/license
files. Font binaries are not redistributed here. See manifests/fonts.toml.

The optional Hyprland profile uses the public Lua API of upstream v0.55.2 and
Debian-packaged desktop components; their licenses remain with those packages.
No third-party dotfiles installer or graphical shell is vendored.
