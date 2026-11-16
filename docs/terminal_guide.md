# Terminal profile

`assets/zsh/` is the single source for managed Zsh modules. Blocks in existing `.zshenv`, `.zprofile` and `.zshrc` load the appropriate modules; `.zshenv` performs no initialization. An existing `ZDOTDIR` is preserved, as is user text around managed blocks.

## 🚀 Daily work

The profile adds `$HOME/.local/bin` to `PATH` without duplicates, selects an available Neovim for `EDITOR` and `VISUAL`, loads local completions and configures a short prompt and history rules. An existing `~/.zsh_history` stays in use. New installations store history under `${XDG_STATE_HOME:-$HOME/.local/state}/zsh/history`; explicit `shell apply` creates its directory. Codex completion is also generated during apply, rather than whenever a terminal opens. Startup files do not launch projects, VPN connections, installers, package synchronization or network processes.

```zsh
setup-system projects list
projectCd
projectCd --name serrja/setupSystem
setup-system projects open --name serrja/setupSystem
setup-system projects open --name serrja/setupSystem --tmux
```

`projectCd` uses the same catalog picker as `projects path` and changes cwd only after receiving a valid path. An empty choice leaves cwd unchanged. Paths with spaces and non-ASCII characters pass without `eval`. See the [Neovim guide](neovim_guide.md) for editor work and the [workspace guide](workspace_guide.md) for project layout and relocation.

## 🛠 Setup and diagnostics

The general `setup-system plan` and `apply` commands support Debian 12/13, subject to profile capabilities. On Debian, `apply --profile terminalDev --yes` installs Zsh when needed, applies this profile and requests a login-shell change for the current user through `chsh`. The main Hyprland installer preserves the existing login shell; it does not make that optional change automatically.

`setup-system shell plan` and `shell apply --yes` configure an already installed Zsh for the current Linux user, including on Ubuntu. Apply makes a private backup, validates startup in isolation, writes managed files and invokes `chsh` only for a shell registered in `/etc/shells`. If PAM requires interactive authentication, run the displayed `chsh` command in your own terminal; passwords never pass through CLI arguments. A new child login shell checks startup; graphical login needs separate verification. Existing terminals and Bash/POSIX scripts are not replaced.

```bash
setup-system shell plan
setup-system shell apply --yes
setup-system shell doctor
getent passwd "$(id -un)"
```

If startup fails, open clean Zsh with `zsh -f` or use the previous login shell. `setup-system shell restore --backup ID --yes` restores only selected managed files whose bytes have not changed since apply. If the saved previous shell was `/bin/bash`, restore it with `chsh -s /bin/bash`. Preserve history and user additions. Never source installer code from `.zshrc` or add `exec zsh` to Bash startup files.
