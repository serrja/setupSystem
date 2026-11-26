# Terminal and shell

Press **Super+Enter** in the managed Hyprland session to open Foot with the desktop palette and fonts. [Desktop theme](../../hyprland/docs/theme.md) · [Fonts](../../hyprland/docs/fonts.md)

## 🐚 Configure Zsh

On the **Zsh** card, preview **Configure** to review changes to an existing Zsh installation. Apply checks startup in an isolated home and writes only the reviewed user files. It preserves existing text outside the managed blocks and keeps the account's login shell. An existing `HISTFILE` takes precedence, followed by an existing `~/.zsh_history`. A new profile otherwise uses `$XDG_STATE_HOME/zsh/history`.

The `cproj` helper opens the Textual app. Open the **Projects and templates** card to select an exact local checkout and launch Neovim or Codex from that directory. The **Install** action on the **RTK** card has its own confirmation. [Workspace projects](../../projects/docs/workspace_guide.md) · [RTK](../../rtk/docs/rtk.md)

If startup fails, use `zsh -f` or the previous shell to inspect the files. Open **Managed resources** and choose the relevant file operation for rollback. Review the listed paths and conflicts before applying recovery. [Recovery](../../../docs/recovery.md)
