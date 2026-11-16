# Linux workstation capabilities

The public installation path is `sudo bash ./setup.sh` on an existing minimal Debian
system. The [Debian prerequisites](debian.md) and [Hyprland guide](hyprland.md) describe
its actual compatibility and login path. General setupSystem capabilities support
Debian 12/13 on x86_64/arm64; the Hyprland profile currently requires Debian 13/trixie
and verified official backports. No other distribution is implicitly compatible.

`manifests/platforms.toml` owns release/architecture aliases and capability data.
`planning.py` performs detection and read-only planning. The pure
`adapters/debian/hyprland.py` selects the reviewed package set; the privileged bootstrap
owns APT execution. `application/workstation.py` owns managed user configuration,
conflict checks, backups and restore. `assets/hyprland/` targets the pinned compositor
version. Portable [fonts](fonts.md), terminalDev, projects, templates and application
operations retain their separate useful functions.

User-only `workstation plan`/`doctor` inspect files without installing prerequisites.
`workstation apply --profile hyprland --yes` configures an already prepared compatible
system; it does not become a second privileged package installer. Normal fresh-machine
installation uses the canonical shell entrypoint once.
