# Terminal profile

`assets/zsh/` is the sole source of the managed Zsh modules. A managed block in the existing `~/.zshrc` loads `${XDG_CONFIG_HOME:-$HOME/.config}/zsh/init.zsh` for interactive sessions. `ZDOTDIR` is not changed, so the ordinary HOME startup-file discovery remains in place. Existing user text around the managed block is preserved and is not copied into the source assets. There are no custom `.zshenv`, `.zprofile` or `.zlogin` files because this profile needs no global or login-only startup work.

## 🚀 Daily work

The profile adds `$HOME/.local/bin` once to `PATH`, selects `EDITOR`/`VISUAL` only when unset, loads local completion definitions without a startup dump file, and uses a small prompt and history policy. An existing `~/.zsh_history` remains in use; a new setup uses `${XDG_STATE_HOME:-$HOME/.local/state}/zsh/history`, whose directory is prepared by the explicit Debian apply. The Codex completion source is generated at apply time, never when opening a terminal. No project, VPN, installer, package sync or network process starts from the shell files.

```zsh
setup-system projects list
projectCd
projectCd --name serrja/jobdesk
setup-system projects open --name serrja/jobdesk
setup-system projects open --name serrja/jobdesk --tmux
```

`projectCd` asks the same catalog-backed picker used by `projects path` and changes cwd only after a valid path is returned. Empty selection leaves cwd unchanged. Paths with spaces or non-ASCII characters are quoted and never passed through `eval`. The Neovim workflow remains in the [Neovim guide](neovimGuide.md); the [workspace guide](workspaceGuide.md) owns layout and relocation.

## 🛠 Installation and diagnosis

`setup-system plan` and `apply` keep the Debian 12/13 gate. On the current Ubuntu host these source files can be checked in isolation, but system apply, live dotfile replacement and login-shell changes are not performed. On Debian, review the plan and backup before explicit apply; then verify installed Zsh, `/etc/shells`, the account's current login shell, terminal and tmux configuration before a separately authorized `chsh` for this user. An interactive subprocess checks startup syntax but **does not** verify the next login. Existing terminal sessions and Bash/POSIX scripts stay in place.

```bash
for source in assets/zsh/*.zsh; do zsh -f -n "$source"; done
setup-system doctor
getent passwd "$(id -un)"
```

For a broken user profile, start a clean Zsh with `zsh -f` or use the previous login shell. Restore only reviewed managed dotfile/config changes from the private backup described in [recovery](recovery.md); preserve history and custom user fragments. Do not source installer code from `.zshrc` or use `exec zsh` in Bash startup.
