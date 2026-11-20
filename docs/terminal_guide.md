# Terminal and shell

The managed Hyprland terminal is Foot with the shared Catppuccin Mocha Green/font policy.
Workspace selects developer packages; Workspace → Tools → shell configures an already
installed Zsh under the current account with backups and startup validation.

Opening Textual or a shell never starts an installer or network download. Existing
terminals, Bash/POSIX scripts, history and login shell are preserved by base installation.
A login-shell change remains a separate native account operation, not an automatic
side effect of selecting Hyprland.

The `cproj` helper opens Textual workspace tools. Project paths use the catalog's exact
Git roots, preserving dirty files and published repository identities. Neovim, fzf and
Git integration are described in [Neovim](neovim_guide.md), [workspace](workspace_guide.md)
and [Git profiles](git_profiles.md).

For broken Zsh startup, use `zsh -f` or the previous shell. Tools → shell_restore restores
the selected unchanged managed files by Backup ID. Do not source installation code from
startup files or replace Bash with `exec zsh`.
