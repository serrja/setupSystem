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

The Hyprland profile uses the public Lua API of upstream v0.55.2 and
Debian-packaged desktop components; their licenses remain with those packages.
No third-party dotfiles installer or graphical shell is vendored.

## Terminal application

Textual is an MIT-licensed runtime dependency from Textualize. It is installed from
the locked Python distribution; its own license remains in that distribution. No
Textual source is vendored in setupSystem. See https://github.com/Textualize/textual.

## Catppuccin Neovim theme

The optional Neovim setup fetches `catppuccin/nvim` at the revision in
[the editor manifest](manifests/editor.toml) under its upstream MIT license.
The checkout retains its upstream license. No theme plugin implementation is vendored.

## Privileged validation runtime

The release includes the upstream wheels of Pydantic, Pydantic Core, annotated-types,
typing-extensions and typing-inspection for the isolated system command validator.
Each unmodified distribution contains its original license under
`*.dist-info/licenses/LICENSE`. Versions and SHA-256 digests come from [uv.lock](uv.lock).
