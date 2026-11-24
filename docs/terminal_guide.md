# Terminal and shell

Press **Super+Enter** in the managed Hyprland session to open Foot with the desktop palette and fonts. [Desktop theme](theme.md) · [Fonts](fonts.md)

## 🐚 Configure Zsh

In **Working environment**, choose **Preview shell setup** to review changes to an existing Zsh installation. Apply checks startup in an isolated home and writes only the reviewed user files. It preserves existing text outside the managed blocks and keeps the account's login shell. An existing `HISTFILE` takes precedence, followed by an existing `~/.zsh_history`. A new profile otherwise uses `$XDG_STATE_HOME/zsh/history`.

The `cproj` helper opens the Textual app. Use **Catalog projects** to select an exact local checkout and launch Neovim or Codex from that directory. The independent **Preview RTK install** action has its own confirmation. [Workspace projects](workspace_guide.md) · [RTK](rtk.md)

If startup fails, use `zsh -f` or the previous shell to inspect the files. Open **Working environment**, choose the relevant backup and select **Plan restore**. Review the listed paths and conflicts before applying recovery. [Recovery](recovery.md)
