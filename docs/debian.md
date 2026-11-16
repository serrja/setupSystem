# Debian prerequisites

```bash
sudo bash ./setup.sh
```

setupSystem configures an **existing minimal Debian installation** with a complete
Hyprland user profile. Obtain and verify the local source/release bundle first. The
machine needs working Bash, APT/dpkg, a network connection and a normal account already
authorized to use sudo. The installer prepares missing Python, pinned uv/runtime and
locked application dependencies itself. Codex, gh, Git, Python, pip, uv, a virtualenv
and an existing compositor are not execution prerequisites.
An existing Python does not imply ready TLS trust. The shell separately checks the
installed `ca-certificates` package and its readable, nonempty regular CA bundle.
After consent, signed Debian bootstrap prepares missing Python/CA; if the bundle
is absent, the bounded Debian `update-ca-certificates` command rebuilds it without
resetting the configured trust selection. A failed readiness check stops before
any HTTPS subscription or client download. Plan/help never repairs trust.

The profile uses Debian's default systemd 257/logind and PAM login, with an accessible
systemd 257 system manager. It does not replace
a different init system. The Hyprland target is Debian 13 / trixie on amd64 or arm64; it is narrower than the
general Debian 12/13 setupSystem matrix. The target's `/etc/os-release` and architecture
are checked before application. Other releases/codenames/architectures receive an
explicit unsupported error. This project does not create an ISO, partition disks,
reinstall Debian or remove a previous desktop.

## Inspect and apply

From another working directory, the same entrypoint works by absolute path:

```bash
sudo bash "$HOME/Projects/serrja/setupSystem/setup.sh"
```

For read-only inspection use `bash ./setup.sh plan` or `bash ./setup.sh --dry-run`.
Help and preliminary planning work without root or Python and perform no downloads,
APT refresh or file changes. Apply shows the whole download/offline-apply plan,
takes one confirmation, then requests the HTTPS subscription with hidden input.
Without a TTY it requires both explicit `--yes` and `--vpn-config-file PATH` before
even preparing missing Python. The file must be absolute, under the verified HOME,
user-owned, regular with one link, mode 0600 or stricter, and 1..8192 bytes. No
symlink or traversal is accepted; the input file is preserved. Never pass the
subscription URL as a shell argument. Invalid or retired arguments stop safely.

The temporary client needs a supported VLESS node from a plain URI/list, a
base64-encoded VLESS list, or compatible Xray JSON, using TLS or REALITY.
An HTTPS address secures retrieval; it does not guarantee a compatible or secure
payload. Encrypted Happ payloads and unsupported provider formats cannot bootstrap
this console stage. Import those through Happ's native GUI after login.

The sudo caller is checked against passwd/group identity before use. Direct root
login needs `--user NAME`; there is no automatic UID-1000 fallback. The user stage
runs with that account's UID/GID/groups and verified HOME. Optional XDG paths are
explicit arguments inside that HOME, not a reason to use `sudo -E`.

A managed source file enables signed official trixie-backports without rewriting the
owner's sources. Only the selected compatible packages and their dependencies are
installed. APT simulation and `--no-remove` stop unexpected removals. There is no
blanket distribution upgrade, desktop metapackage, package purge or automatic cleanup.
Network connections, locale, timezone and the current session remain in place.


The temporary Xray client runs as the target user with a loopback-only HTTP
listener. Happ, signed APT packages, runtime dependencies and font archives are
verified while that connection exists. The installer stops the client and removes
its temporary configuration before offline APT in a network namespace with systemd
offline service handling. Native Happ helper activation follows as a separate system
operation outside that namespace, preserving an already running helper. User
configuration then runs in its own offline network namespace. Final subscriptions
are imported through Happ's GUI after login; existing Happ settings/database stay intact.

The first Debian bootstrap and the subscription endpoint need initial network
access. The pinned Xray client also needs initial access, unless its matching
verified archive is already in the source bundle's `downloads/` directory:
`xray-v26.11.9-amd64.zip` or `xray-v26.11.9-arm64.zip`. The
[network manifest](../manifests/network.toml) pins the source and SHA-256. A VPN
cannot fetch its own initially blocked client or subscription automatically.

## Login, repeat and recovery

After successful installation, log in locally as the target user and run the exact
session command printed by the installer. The default is
`"$HOME/.local/share/setup-system/bin/start-hyprland"`. The installer never starts a
compositor as root or interrupts the current session.

Rerun the same sudo command to resume unfinished stages. An unchanged user profile
creates no duplicate configuration or backup. Unknown or locally edited managed files
require review. A partial failure returns nonzero and identifies the failed stage;
APT changes are not promised to roll back transactionally. See
[Hyprland](hyprland.md) for keys/overrides and [recovery](recovery.md) for backup use.

Current local evidence is fixture/mock testing. A real disposable Debian guest must
separately verify the one-command install, repeat apply and graphical session; the
coordinator's development machine is never selected as an installation target.
