# Linux workstation capabilities

The public first-launch path is `sh install.sh --release vX.Y.Z` from an inspected,
versioned GitHub release on an existing minimal Debian system. Direct root login
adds `--user NAME`. The [Debian prerequisites](debian.md) and
[Hyprland guide](hyprland.md) describe compatibility and login. General setupSystem
capabilities support Debian 12/13 on x86_64/arm64; Hyprland requires Debian 13/trixie
and verified official backports. No other distribution is implicitly compatible.

## Capability and execution boundaries

`manifests/platforms.toml` owns Debian codenames, architecture aliases and package
capabilities. The stdlib `capabilities.py` validates them into immutable typed models.
`planning.py` performs detection and read-only planning; the Debian adapters consume
the same policy. Invalid lists, package pins or mismatched release metadata fail
before execution.

`application/installer.py` owns the installation use case shared by Textual and Typer:
typed request/plan, consent, application progress, ordered user/system operations,
failure reporting and recovery. Its ports isolate runtime preparation, temporary
networking and package sessions. `installation.py` validates privileged paths,
identities and side effects; `adapters/debian/package_session.py` owns pure APT
source/command construction. The shared `installation_state.py` owns journal stages,
statuses and serialization for both application and package progress. Package operations
keep their signed-source, no-removal and offline-apply boundaries.

`system_inspection.py` reads existing root/HOME encryption and clock state through
bounded read-only queries. Unknown encryption does not mean unencrypted. It does not
create encryption or request keys. `desktop_system.py` owns a finite typed system plan:
graphical login, its selected-user bridge, existing GRUB settings and an optional
validated timezone. The application records `system-desktop` only after this stage
succeeds; a failure retains completed user stages and returns nonzero.

`downloads.py` owns bounded HTTPS reads, permitted redirect hosts and explicit proxy
policy. Runtime, application artifacts and fonts reuse that transport while retaining
their own pinned hashes and receipts. The font archive identity comes from
`fonts.toml` through `font_policy.py`, including cache preflight destinations.

`application/user_profiles.py` owns the preserved base, terminalDev and Happ user
use cases. Their Typer commands only translate options, presentation and exit codes;
application services do not import CLI helpers. Shell configuration remains an
application operation with the same explicit login-shell behavior.

`tui/installer.py` presents the Textual application; widgets perform no package
operations. `cli/install.py` exposes explicit automation and recovery commands.
Both use `InstallationService` and its typed `Interaction` port. The private stdlib
worker handles checked root-to-user transitions. The small `install.sh` delivers
pinned uv/Python and the locked CLI as the ordinary user, then opens Textual.
Initial CA trust and access to the delivery hosts remain prerequisites. Release delivery
is the fresh-system path; manual `.venv` preparation is a development concern.

## Apply and recovery

The selected profile additionally requires systemd 257 as client and accessible
system manager; native preflight inspects it without starting services.
The user phase first prepares the runtime and invokes
`setup-system workstation prefetch --profile hyprland` while the temporary
loopback proxy is available. After client/config cleanup, APT runs in its offline
network namespace. Native Happ helper activation is a separate system operation
outside that namespace and preserves an already running helper. The user phase then
calls `setup-system workstation apply --profile hyprland --yes --offline` inside
its own offline network namespace.
`application/workstation.py` owns managed configuration, conflict checks, backups
and restore, and refuses Hyprland apply as root. `assets/hyprland/` targets the
pinned compositor version, including the explicit compatible `hyprland-guiutils`
backport. One session-owned panel bridge adapts Debian Waybar 0.12 workspace buttons
to Hyprland's Lua dispatch API. Native modules display real hardware and local time:
only present system batteries and supported backlights are selected, and the clock
tooltip exposes the timezone. Optional session helpers fail independently and log
under the selected user's state directory. Portable [fonts](fonts.md), terminalDev,
projects, templates and application operations retain their useful functions.

After user configuration, the root desktop stage configures Debian greetd, gtkgreet
and Cage for the next normal boot. The existing ordinary account authenticates through
PAM, then the selected user's launcher starts Hyprland. It does not start the greeter
or compositor immediately, enable autologin, interrupt the current session or reboot.
Existing PAM and disk-unlock integration are preserved; a locked or unverifiable
encrypted HOME is a preflight error. GRUB is retained with a hidden menu and timeout
zero, including recordfail. No boot entries or encryption settings are removed; no
recovery-key waiting window is guaranteed. The local TTY launcher remains a fallback.

A blank Textual timezone field or omitted `--timezone` preserves the current zone.
An explicit IANA zone is validated before apply. Existing NTP providers are retained;
the plan adds a default provider only when inspection conclusively finds none. Unknown
provider state does not authorize replacement. Clock adjustments are not transactional.
See [Hyprland login and controls](hyprland.md) and [recovery](recovery.md).

Interactive installation uses hidden HTTPS subscription input after plan consent.
Unattended `--yes` requires `--vpn-config-file` pointing to an existing private,
bounded, single-link regular user file under verified HOME. Only the filename
crosses the private source handoff; the file is preserved. Read-only
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
