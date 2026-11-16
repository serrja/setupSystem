# Neovim and terminal work

This guide covers setupSystem's installed `terminalDev` profile. The editor opens files and performs explicit actions; project commands and Git operations remain under your control. Configuration source lives in `setupSystem/assets/nvim`, the managed copy in `${XDG_CONFIG_HOME:-$HOME/.config}/nvim`, and plugins/parsers in XDG data. On supported Debian, review `plan` and backup before updating with `setup-system apply --profile terminalDev --yes`. Existing user files do not make the Debian-only installer supported on Ubuntu. Any separately scoped user-configuration apply still needs appropriate permission and backup.

## 🚀 Quick start

```bash
setup-system doctor --profile terminalDev
setup-system projects list
setup-system projects open --name serrja/jobdesk
```

For another project, use its exact name from the list. Without `--name`, the command offers a picker; an empty answer cancels launch. `--tmux` creates or attaches a separate session when `tmux` is installed, otherwise it reports an error. Projects open from a specific root; `.env` and `.envrc` are not executed.

The editor starts in **normal mode**. Press `i` to enter text and `Esc` to return to normal mode. `:w` saves, `:q` closes the window, `:wq` saves and closes, and `:q!` closes **discarding unsaved changes**. Built-in help is available through `:help` and `:checkhealth`.

## ⌨️ Keys

`Space` is the leader. `Space ?` opens the which-key reference; `Space gD` shows the current file's Git diff. Press each sequence in order, rather than holding its keys simultaneously. These mappings use normal mode unless stated otherwise.

| Action | Keys | Requirement or note |
| --- | --- | --- |
| Save / close window | `Space w` / `Space q` | Current buffer/window |
| Find file / text / open buffer | `Space ff` / `Space fg` / `Space fb` | Project search uses ripgrep |
| File / workspace symbols; file browser | `Space fs` / `Space fS` / `Space e` | Symbol search needs an LSP server |
| File outline | `Space o` | Aerial can use a parser without LSP |
| Incoming / outgoing LSP calls | `Space ci` / `Space co` | Server call-hierarchy support |
| Implementations / previous / next buffer | `gI` / `[b` / `]b` | Implementations need an LSP server |
| Toggle soft wrapping | `Space uw` | Display setting |
| Vertical / horizontal split | `Space sv` / `Space sh` | Window layout |
| Terminal split | `Space tt` | Press `Esc` twice to leave terminal mode |
| Definition / references | `gd` / `gr` | Available LSP server |
| Rename symbol / LSP action | `Space rn` / `Space ca` | Available LSP server |
| Diagnostics / previous / next | `Space dd` / `[d` / `]d` | Available diagnostics |
| Format explicitly | `Space cf` | Available formatter; disabled for vaults |
| Git status / hunk / blame | `Space gs` / `Space gh` / `Space gb` | Git repository |

`Space ff` and `Space fg` run ripgrep in the nearest project inside the session root. Starting from an owner directory without project markers is rejected; an empty owner-level `.git` does not establish a project. Search excludes `.env*`, key/credential directories, private state, caches and backups, while ordinary authentication source such as `authService` remains visible. It does not follow symlinks. These filters are not OS access controls; an already opened secret buffer can appear in the buffer list. Do not open private data in an untrusted checkout.

`Space cf` is disabled for vaults and never runs on save. Formatters are Ruff for Python, StyLua for Lua, `clang-format` for C/C++, and `dart format` for Dart, when the corresponding executable is available. Vue/TS/JS use Prettier only when the project root has an explicit configuration. Native EditorConfig takes precedence over default indentation. Project CI commands and configuration remain authoritative.

## 🔎 Everyday workflow

1. Run `setup-system projects list`, then `setup-system projects open --name serrja/jobdesk`. In Neovim, press `Space ff`, type part of a filename and press Enter.
2. Open a Python file. With basedpyright running, `gd` jumps to a definition, `gr` lists references and `Space rn` renames through LSP. Review changes before saving. LSP selects the opened root's `.venv/bin/python` when present; it does not create the environment.
3. Press `Space sv` for another window and `Ctrl-w w` to switch windows. `:ls` lists buffers; `:bnext` selects the next. `Space tt` opens a terminal for the project's documented command. Press `Esc` twice to leave terminal mode.
4. Inspect a hunk with `Space gh` or status with `Space gs`; then run `git diff --check`, `git diff` and targeted `git add <path>` in the terminal. Review staged changes and the outgoing range before manually committing or pushing. The editor does not do those operations automatically.

Within Neovim, `v` selects, `y` copies and `p` pastes. System clipboard access is not enabled automatically: Debian, SSH and tmux need an appropriate provider. Check `:checkhealth provider` and use its system register only after verifying access. Oil/Aerial/devicons PUA icons need a terminal font containing those glyphs; `guifont` does not change a TUI terminal. The pinned JetBrainsMono Nerd Font Mono and its license are described in the [font manifest](../manifests/fonts.toml). Explicit font apply uses `setup-system workstation apply --profile fonts --yes`; see [fonts](fonts.md). Emoji use a separate fallback such as installed Noto Color Emoji. Do not globally change `TERM`, `LC_ALL` or `ambiwidth` to fix glyphs.

## 🧪 Checks and recovery

```bash
setup-system plan --profile terminalDev
setup-system doctor --profile terminalDev
nvim --headless '+checkhealth' '+qa'
nvim --clean
setup-system backup --profile terminalDev
setup-system restore --backup BACKUP_ID --dry-run
```

`nvim --clean` helps diagnose problems without this configuration; it is not an OS sandbox. Use it in untrusted checkouts and do not enable project-local hooks, `.nvim.lua`, `.exrc`, `.envrc` or automatic tasks. The normal configuration disables project-local plugin specs, modelines, session history and plugin installation/updates at startup. Plugins execute with the current user's permissions.

For missing LSP, inspect `:LspInfo`, `:checkhealth vim.lsp`, `:echo getcwd()` and `setup-system doctor --profile terminalDev`. The root must be the nearest project inside the selected repository, `.venv` must belong to that project, and `basedpyright-langserver` must be in `PATH`. C/C++ needs `clangd` and the project's real `compile_commands.json`; opening a file does not run CMake. Inspect missing parsers/formatters with `:checkhealth nvim-treesitter` and `:ConformInfo`, then perform an explicit `setup-system apply --profile terminalDev --yes` on a supported target. For plugin incompatibility or update errors, compare `manifests/terminalDev.toml` and `assets/nvim/lazy-lock.json`; the installer refuses to overwrite an unknown checkout.

Check `command -v nvim` and `nvim --version` for PATH issues, `:checkhealth provider` for clipboard issues, and the terminal's actual font family/Nerd Font version for missing icons. Aerial's outline can use a parser without LSP. Call hierarchy requires server support and does not describe runtime architecture between services. Headless or syntax checks do not prove interactive windows, clipboard behavior or another OS; check those in the appropriate session.

## Language tools

A server activates only when its executable is in `PATH`: basedpyright for Python, clangd for C++, lua-language-server, typescript-language-server, dart, vscode-json-language-server, yaml-language-server and marksman. A Lua configuration does not prove a server is installed. Vue additionally needs vue-language-server and a compatible `@vue/typescript-plugin` under `stdpath("data")/tools/node_modules`; incomplete Vue tooling does not enable a partial Vue LSP. Install pinned tools explicitly; editor startup performs no downloads.

The [terminalDev manifest](../manifests/terminalDev.toml) pins plugins and parsers; standard lazy lock repeats plugin commits. Opening a shell/editor does not install/update tools, execute project-local Lua, run tests or build. Run the project's documented checks explicitly after verifying dependencies; this repository's automatic CI runs its applicable gate on PRs and permanent-branch pushes.

To check rendering, compare clean/configured Neovim and the terminal outside/inside tmux using Cyrillic, box drawing, PUA icons, emoji, a compound emoji, a long line and a tab. Headless width checks do not prove actual terminal cell geometry. A native GUI observation, file-state check and parser-loading check are separate results.
