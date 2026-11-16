# Happ: installation and settings recovery

Happ owns subscriptions, routing profiles and connections. setupSystem verifies its package, makes private backups and reports native autostart state. Private links are neither committed nor printed by these commands. The graphical client owns native subscription import after login.

## 🚀 Install

The [platform manifest](../manifests/platforms.toml) owns the support matrix. The general Happ package adapter supports Debian 12/13 on x86_64/arm64; the Hyprland profile has a separate, narrower capability gate. Use a prepared setupSystem runtime for optional application operations. On Ubuntu, safe read-only diagnostics are available, while package setup, import and CLI restore stop as `UNSUPPORTED`.

```bash
setup-system plan --profile happ
setup-system happ setup --yes
setup-system happ doctor
```

A repeated setup preserves an already installed Happ. For a missing Debian client, the manifest pins official release 4.5.4, each `.deb` SHA-256 and package metadata checks before targeted `sudo apt-get install`. Without available sudo, the operation blocks before downloading. Validate the selected package and GUI separately on Debian. A release digest is not an independent publisher signature. The pinned package ships root-run `happd.service`; its own post-install script enables and restarts that daemon. Package installation does not verify a graphical session or a working connection.

## Download preparation before graphical login

When the installer uses a temporary console proxy for downloads, that temporary client
is separate from Happ's final graphical session. The download phase verifies the pinned
Happ package and font archive before the temporary connection is stopped. It does not
import a subscription into Happ or change Happ's private database, routing or settings.
Complete any final subscription import with Happ's native GUI after logging in.
The one-command Hyprland installer requires manual review of a different or partially
installed Happ package version; it does not force an upgrade or restart an active helper.

The prepared user runtime can prefetch the font archive with
`setup-system workstation prefetch --profile hyprland` without Hyprland, fontconfig or
a running graphical session. A later `setup-system workstation apply --profile hyprland --yes --offline`
uses only the verified user cache when font files are needed. Missing, changed or unsafe
cache entries stop the operation; already matching installed fonts need no download.
These are installer stages, not additional required commands for the one-command setup.
Cache provenance records the public pinned release URL and digest, never a provider's
private subscription URL. A verified download is not a successful GUI or VPN check.

## 🔒 Backup and restore

On Debian, close Happ normally only when doing so will not interrupt a required connection. Then run:

```bash
setup-system happ backup
setup-system happ restore --backup BACKUP_ID
```

Backups live under `${XDG_STATE_HOME:-$HOME/.local/state}/setup-system/backups/<id>/` with a private manifest. They preserve `Happ.conf`, routing settings/cache and the native autostart entry. Subscription SQLite databases use SQLite's backup API plus `integrity_check`, rather than copying a live database file. Links and rules remain in the private snapshot. Backup blocks while Happ is running; prefer Happ's native export for a running client.

Preview makes no changes. Before replacement, Happ must be closed, WAL/SHM files absent and any conflict reviewed:

```bash
setup-system happ restore --backup BACKUP_ID --apply --yes --replaceCurrent
```

Restore first makes another private snapshot of current state. Happ's internal directory is not a portable export: use the installed version's compatible native export/import. Login registration and system permissions need separate handling. Do not transfer device identity.

## 🔗 Subscriptions and separate configurations

Links are optional; empty input skips import. For one or more links:

```bash
setup-system happ setup --yes --interactive
setup-system happ addSubscription
```

When `wl-copy` or `xclip` is available, the CLI accepts hidden input in a local terminal and hands it to the clipboard. Use Happ's native import-from-clipboard control and verify the result there before entering another link. The CLI clears its clipboard handoff after confirmation, but a history manager may retain a copy; enter secret links directly in Happ when that is safer. Without a clipboard tool, it reports `needsManualAction` before asking for a link.

Web subscriptions, `happ://crypto…` and configuration links are classified separately. These GUI handoff commands do not download, decrypt or log their content. If the result is uncertain, inspect Happ's list and name collisions before retrying. A subscription can change application settings.

## 🧭 Routing

```bash
setup-system happ configureRouting
```

Existing rules, DNS, TUN and exclusions remain owned by Happ. In newer versions, routing profiles belong to subscriptions; a managed JSON subscription may disallow manual edits. `happ://routing/add/…` adds a profile, but the first profile can become active after geo-file downloads. `happ://routing/onadd/…` also activates or replaces a profile, so the CLI never hands it off automatically. A matching name may replace a profile.

Saving a rule and activating it on the current connection are separate: the [Happ routing documentation](https://www.happ.su/main/dev-docs/routing) describes reconnection requirements. The CLI does not reconnect VPN or independently change fallback/DNS settings. Review these behaviors against the installed Happ version before changing a required connection.

## ▶️ Native autostart

```bash
setup-system happ configureAutostart
setup-system happ doctor
```

On Debian, the command reads Happ's setting and the desktop entry created by Happ itself. If both are enabled, it reports `unchanged`; otherwise enable launch-at-login in the installed Happ version and repeat the check. Autoconnect and subscription updates are separate settings that the CLI does not change. Entry registration does not prove startup on the next login, so that scenario remains `needsManualCheck`.

## 🛠 Diagnostics

`happ doctor` reports only version/source, process presence, autostart/autoconnect/routing categories and profile counts. It does not verify the GUI, servers, DNS/IPv6 protection or absence of leaks. Inspect the plan with `setup-system plan --profile happ`. Do not disconnect an active VPN just to update it; rules requiring reconnection stay deferred until an explicit decision.

Official references: [downloads](https://www.happ.su/main), [requirements](https://www.happ.su/main/faq/requirements), [adding links](https://www.happ.su/main/faq/adding-configuration-subscription), [routing](https://www.happ.su/main/dev-docs/routing).
