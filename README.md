# setupSystem

Debian workstation setup with shared `terminalDev` and Happ profiles. The stdlib bootstrap checks the host before downloads or writes, then uses pinned `uv` and a separate project environment for the Typer CLI. The only installer target is Debian 12/13 on x86_64 or arm64; `terminalDev` on arm64 remains blocked until its archives are pinned. See [platforms.toml](manifests/platforms.toml).

## 🚀 Quick start

Requires Debian 12/13 and Python 3.10+ as `python3` for bootstrap. On every other host, including the current Ubuntu 26.04 machine, `plan` gives read-only `UNSUPPORTED` diagnostics and `apply` stops before installation or configuration. See the [recovery guide](docs/recovery.md). `plan` needs no package installation and makes no changes.

```bash
python3 bootstrap.py --help
python3 bootstrap.py plan
python3 bootstrap.py plan --profile terminalDev
python3 bootstrap.py apply --yes
python3 bootstrap.py apply --profile terminalDev --yes
python3 bootstrap.py plan --profile happ
setup-system doctor
```

Run bootstrap from the actual checkout root. On Debian, base `apply` prepares pinned `uv` 0.12.15, installs a uv-managed Python 3.14, checks `uv.lock` with `--locked`, installs a wheel-backed `setup-system` CLI independent of the checkout path, backs up existing files, and installs user configuration. The `terminalDev` profile additionally installs pinned Neovim, plugins, parsers and editor tools; missing Debian packages use targeted `sudo` only when available. The Happ profile preserves an existing client and uses a pinned official package only if absent; see the [Russian Happ guide](docs/happGuide.md). Blocked package steps remain blocked. No profile changes the login shell or Docker state. Re-run after moving the checkout: installed configuration lives under XDG paths.

## 🧭 Boundaries

- `bootstrap.py` is stdlib-only. `src/setup_system/planning.py` is the read-only platform gate.
- The Typer CLI coordinates use cases; `storage.py` owns atomic files and private backups.
- `assets/zsh` is sourced by a managed block in `~/.zshrc`; shell startup runs no installer, network call or `sudo`. See the [terminal guide](docs/terminalGuide.md).
- [Project workspace](docs/workspaceGuide.md) explains the one catalog, owner layout and read-only relocation plan. No checkout moves during apply.
- `assets/agents` is copied with hashes to `~/.agents`; Codex reads a copy at `$CODEX_HOME/AGENTS.md`. Local scopes stay in `$XDG_CONFIG_HOME/agents/scopes.toml`.
- [Agent tool inventory](manifests/agentTools.toml) records selected skills, observed plugin/MCP versions and their access limits; it is metadata, not a permission grant or plugin installer.
- `manifests/applications.toml` documents sources, Debian compatibility and detection. Debian `zsh` and Docker Engine have explicit catalog install actions; other entries show current state and an explicit block when an adapter cannot verify the source.
- `manifests/terminalDev.toml` pins editor archives, plugins and tools. `assets/nvim` and `assets/tmux` are installed under XDG config only by explicit `terminalDev` apply. See the [naming policy](docs/naming.md) and [Neovim guide](docs/neovimGuide.md).
- `manifests/happ.toml` pins official desktop release assets; Happ owns its private subscriptions, routing and native autostart. `setup-system happ doctor` reports safe categories only.

## 🛠 Daily commands

```bash
setup-system plan
setup-system apps list
setup-system agents doctor
setup-system agents launch serrja/jobdesk --mode review
setup-system templates list
setup-system projects list
setup-system projects planMigration
setup-system happ doctor
setup-system apps install docker
setup-system backup
setup-system backup --include-vault --vault "$HOME/Obsidian Vault/Хранилище"
setup-system restore --backup BACKUP_ID --dry-run
```

For project editing, launch a separate session from the exact repository root with `--mode project`. The local Codex default is read-only. Permission profiles are beta and require a fresh scoped session; a policy file alone is not an access boundary.

## 🔒 Recovery

Backups and manifests are private under `${XDG_STATE_HOME:-$HOME/.local/state}/setup-system/backups`. `restore` checks hashes and previews by default; `--apply --yes` writes verified files. Preserve private application data, auth, vault content and Jobdesk database backups separately. Do not put tokens, browser profiles, cookies, SSH keys or these backups in a public repository.

`Docker`, Compose, Buildx and the existing ChatGPT `.deb` are detected without changing their data. Their current installation on Ubuntu does not prove Debian GUI or daemon behavior. Use [recovery notes](docs/recovery.md) for manual system packages and the limits of each check.

For account setup and daily prompts, see the [Russian ChatGPT guide](docs/chatgptGuide.md). Its suggested instructions are not applied to the account automatically.

Contribution and PR commands are in [CONTRIBUTING.md](CONTRIBUTING.md). No production CD target is configured.
