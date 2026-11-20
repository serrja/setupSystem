# Terminal and shell

Hyprland starts Foot with the shared desktop palette and font policy. Workspace
Tools configures an installed Zsh for the current account. It checks startup in
an isolated home, preserves text outside its managed blocks and uses the existing
history file when available. Selecting the profile retains the current login shell.

The `cproj` helper opens the Textual workspace tools. Project selection resolves
an exact Git root. [Workspace](workspace_guide.md) · [Neovim](neovim_guide.md)

If startup fails, use `zsh -f` or the previous shell. Select the operation in
Recovery, preview its affected files and confirm the restore. [Recovery](recovery.md)
