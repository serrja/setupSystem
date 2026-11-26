# Obsidian

## ⚙️ Select a vault and enable the CLI

Choose an existing vault or use **Create vault** for a new empty folder. Open it in the native vault manager. Turn on **Settings → General → Command line interface** and complete registration. On Linux, Obsidian copies its CLI to `~/.local/bin/obsidian`. The plugin checks this copy against the installed Debian package and confirms the open vault’s full path before requesting a change. Refresh the registration after an installer update if its copy has changed. [Obsidian CLI](https://obsidian.md/help/cli)

## ⚙️ Editor styles and preferences

**Configure an installed CSS snippet** lists snippets discovered by the open vault. Pick a trusted editor style, choose whether to enable it and review the vault, snippet and current state before applying. The plugin checks the enabled list again and records the result for this vault. Choose the same snippet to disable it later. Obsidian also exposes native actions for installed themes and core features. [CSS snippets](https://obsidian.md/help/snippets) · [CLI reference](https://obsidian.md/help/cli)

For editing behavior, open **Settings → Editor**. **Default editing mode** selects Live Preview or Source mode for new tabs. Choose **Show line numbers** in the same settings page to control the gutter. These persistent preferences use native Settings in the interface verified with the 1.15.1 installer. [Editor settings](https://obsidian.md/help/settings#Editor) · [Installer release](https://github.com/obsidianmd/obsidian-releases/releases/tag/v1.15.1)

## ⚙️ Keyboard shortcuts

**Inspect command shortcut** reads the selected command ID in the open vault. To bind it, open **Settings → Hotkeys**, find the command, choose **+**, press the combination and save. Inspect the command again to see the assignment. The documented CLI provides lookup and enumeration. [Hotkey settings](https://obsidian.md/help/hotkeys) · [CLI command reference](https://obsidian.md/help/cli)

## 🔐 Accounts and extensions

Use Obsidian's account and Sync dialogs. Review community plugin publishers individually. Restricted mode is controlled in Obsidian. [Plugin security](https://obsidian.md/help/plugin-security)

## 🧹 User data

Vault folders, notes, attachments and existing CSS files stay under your control during cleanup. [Obsidian storage](https://obsidian.md/help/data-storage)
