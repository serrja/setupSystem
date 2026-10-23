# Linux workstation profiles

setupSystem separates portable user configuration from the package backend and
optional desktop/editor profiles. Debian is the implemented OS backend. Other
systems are not declared supported by matching an `ID_LIKE` value.

## Navigation

[Debian installation and configuration](debian.md) owns ISO/USB/OS steps.
[Hyprland](hyprland.md), [fonts](fonts.md), [terminal](terminalGuide.md),
[Neovim](neovimGuide.md) and [recovery](recovery.md) own their respective guides.

`application/workstation.py` plans portable assets, detects conflicts, backs up and
restores. `adapters/debian/desktop.py` owns package selection and refuses other OSes.
`assets/hyprland/` is the versioned desktop profile; `assets/nvim/` remains the editor
owner. A future distro adds a reviewed backend, not a copied installer or empty
provider. Existing bootstrap, Zsh, RTK and template mechanisms remain their owners.

`workstation plan`/`doctor` only inspect. `apply --yes` writes selected profile files;
`--install-debian-packages` additionally simulates then requests APT installation.
No command selects a login session, removes a DE or restarts the display manager.
An existing Hyprland entrypoint requires explicit reviewed inclusion; unknown local
edits are never overwritten. Put personal additions after the managed include.
