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

The profile uses Debian's default systemd/logind and PAM login. It does not replace
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
APT refresh or file changes. Apply shows its full stages and needs one confirmation;
without a TTY it requires explicit `--yes`. Invalid arguments never select a default
installation accidentally.

The sudo caller is checked against passwd/group identity before use. Direct root
login needs `--user NAME`; there is no automatic UID-1000 fallback. The user stage
runs with that account's UID/GID/groups and verified HOME. Optional XDG paths are
explicit arguments inside that HOME, not a reason to use `sudo -E`.

A managed source file enables signed official trixie-backports without rewriting the
owner's sources. Only the selected compatible packages and their dependencies are
installed. APT simulation and `--no-remove` stop unexpected removals. There is no
blanket distribution upgrade, desktop metapackage, package purge or automatic cleanup.
Network connections, locale, timezone and the current session remain in place.

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
