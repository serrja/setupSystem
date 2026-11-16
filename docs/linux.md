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

The selected profile additionally requires systemd 257 as client and accessible
system manager; shell and native preflight inspect it without starting services.
The user phase first prepares the runtime and invokes
`setup-system workstation prefetch --profile hyprland` while the temporary
loopback proxy is available. After client/config cleanup, APT runs in its offline
network namespace. Native Happ helper activation is a separate system operation
outside that namespace and preserves an already running helper. The user phase then
calls `setup-system workstation apply --profile hyprland --yes --offline` inside
its own offline network namespace.
`application/workstation.py` owns managed configuration, conflict checks, backups
and restore, and refuses Hyprland apply as root. `assets/hyprland/` targets the
pinned compositor version. Portable [fonts](fonts.md), terminalDev, projects,
templates and application operations retain their useful functions.

Before the Python handoff can apply, shell preflight independently checks Python
and Debian CA trust. Missing prerequisites are prepared from signed Debian sources
after the one plan confirmation, with a bounded CA-bundle repair and recheck when
needed. Read-only modes never install packages or rebuild trust.

Normal TTY installation uses hidden HTTPS subscription input after plan consent.
Unattended `--yes` requires `--vpn-config-file` pointing to an existing private,
bounded, single-link regular user file under verified HOME. Only the filename
crosses the private eight-position source handoff; the file is preserved. Read-only
plan/help neither prompts for a subscription nor downloads anything. Initial client
or verified seed availability and subscription reachability remain prerequisites.

The temporary client needs a supported VLESS node from a plain URI/list, a
base64-encoded VLESS list, or compatible Xray JSON, using TLS or REALITY.
An HTTPS address secures retrieval; it does not guarantee a compatible or secure
payload. Encrypted Happ payloads and unsupported provider formats cannot bootstrap
this console stage. Import those through Happ's native GUI after login.

User-only `workstation plan`/`doctor` inspect files without installing prerequisites.
`workstation apply --profile hyprland --yes` configures an already prepared compatible
system; it does not become a second privileged package installer. Normal fresh-machine
installation uses the canonical shell entrypoint once.
