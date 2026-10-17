# Terminal profile

`assets/zsh/` is the sole source of the managed Zsh modules. Managed blocks in the existing `.zshenv`, `.zprofile`, and `.zshrc` load the appropriate modules; `.zshenv` performs no initialization. `ZDOTDIR` is respected and never changed. Existing text around each block is preserved and is not copied into source assets.

## 🚀 Daily work

The profile adds `$HOME/.local/bin` once to `PATH`, selects working Neovim for `EDITOR`/`VISUAL`, loads local completion definitions without a startup dump file, and uses a small prompt and history policy. An existing `~/.zsh_history` remains in use; a new setup uses `${XDG_STATE_HOME:-$HOME/.local/state}/zsh/history`, whose directory is prepared by explicit shell apply. The Codex completion source is generated at apply time, never when opening a terminal. No project, VPN, installer, package sync or network process starts from the shell files.

```zsh
setup-system projects list
projectCd
projectCd --name serrja/jobdesk
setup-system projects open --name serrja/jobdesk
setup-system projects open --name serrja/jobdesk --tmux
```

`projectCd` asks the same catalog-backed picker used by `projects path` and changes cwd only after a valid path is returned. Empty selection leaves cwd unchanged. Paths with spaces or non-ASCII characters are quoted and never passed through `eval`. The Neovim workflow remains in the [Neovim guide](neovimGuide.md); the [workspace guide](workspaceGuide.md) owns layout and relocation.

## 🛠 Installation and diagnosis

General `setup-system plan` and `apply` keep the Debian 12/13 gate. On Debian, `apply --profile terminalDev --yes` installs Zsh if absent, applies this same managed profile and requests the current user's login shell through `chsh`. `setup-system shell plan` and `shell apply --yes` configure an already installed Zsh for the current Linux user, including Ubuntu. Apply creates a private backup, verifies isolated startup, writes managed files, and attempts `chsh` only when the executable is registered in `/etc/shells`. If PAM asks for interactive credentials, perform the reported `chsh` in your own terminal; never pass a password to this CLI. A new child login shell verifies startup, while a full graphical next login remains a separate check. Existing terminal sessions and Bash/POSIX scripts stay in place.

```bash
setup-system shell plan
setup-system shell apply --yes
setup-system shell doctor
getent passwd "$(id -un)"
```

For a broken user profile, start a clean Zsh with `zsh -f` or use the previous login shell. `setup-system shell restore --backup ID --yes` restores selected managed files only if they still match the applied bytes. The prior login shell can be restored with `chsh -s /bin/bash` when that was the recorded previous value. Preserve history and custom user fragments. Do not source installer code from `.zshrc` or use `exec zsh` in Bash startup.
