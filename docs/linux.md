# Linux workstation capabilities

The public installation path is `sudo bash ./setup.sh` on an existing minimal Debian
system. The [Debian prerequisites](debian.md) and [Hyprland guide](hyprland.md) describe
its actual compatibility and login path. General setupSystem capabilities support
Debian 12/13 on x86_64/arm64; the Hyprland profile currently requires Debian 13/trixie
and verified official backports. No other distribution is implicitly compatible.

`manifests/platforms.toml` owns release/architecture aliases and capability data.
`planning.py` performs detection and read-only planning. The pure
`adapters/debian/hyprland.py` selects the reviewed package set; `installation.py`
executes signed Debian package operations and validates privileged file boundaries.
`application/installer.py` owns the installation use case, typed request/plan, consent,
ordered system/user operations, failure reporting and recovery. Its runtime port is
implemented by `runtime.py`, which prepares pinned uv/Python and the locked user
runtime without root build hooks.

`cli/install.py` exposes that same use case through Typer. With an existing runtime,
`setup-system install plan --source /path/to/setupSystem` inspects a verified local
bundle. `setup.sh` remains the one-command clean-machine entrypoint, including a
read-only shell preflight when Python is absent. Its private `_installer_entry.py`
handoff accepts only the already parsed positional request; it has no separate
public command parser or installation logic.

The user phase calls the existing `workstation apply --profile hyprland --yes`
command. `application/workstation.py` owns managed configuration, conflict checks,
backups and restore, and refuses Hyprland apply as root before side effects.
`assets/hyprland/` targets the pinned compositor version. Portable [fonts](fonts.md),
terminalDev, projects, templates and application operations retain their useful functions.

User-only `workstation plan`/`doctor` inspect files without installing prerequisites.
`workstation apply --profile hyprland --yes` configures an already prepared compatible
system; it does not become a second privileged package installer. Normal fresh-machine
installation uses the canonical shell entrypoint once.
