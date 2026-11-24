# Neovim and language profiles

Select **Neovim and Wayland clipboard** in **Working environment**. Plan resolves Debian's Neovim package and the reviewed plugin sources. It records the selected Git commits before Apply. The configuration loads local copies of lazy.nvim, blink.cmp, nvim-treesitter, conform.nvim, fzf-lua, Oil and Gitsigns. Startup disables automatic plugin downloads and update checks. The editor catalog is `/usr/lib/setup-system/config/workspace/editor.toml`.

## 🧩 Select language tools

Choose the languages you use. With Neovim selected, the plan includes the corresponding parsers, language servers and formatters.

| Profile | Language service and formatter |
| --- | --- |
| Python | basedpyright and Ruff in user-local pipx environments. The language server can use the project's `.venv/bin/python`. |
| TypeScript and Vue | TypeScript and Vue language servers, the Vue TypeScript plugin and Prettier from the user-local npm prefix. Formatting requires a Prettier configuration file or a `prettier` field in the project's `package.json`. |
| Dart and Flutter | A user-local checkout from Flutter's official stable ref, with the bundled Dart language server and formatter. |
| C++ | Debian's clangd and clang-format packages. |

Apply installs missing Tree-sitter parsers explicitly. The editor check opens the absolute managed `init.lua` headlessly in a temporary project, parses a buffer with each selected parser, waits for each selected language server and invokes its formatter. It also checks the clipboard commands and, in a Wayland session, the clipboard provider. Inspect a partial result if packages or files changed before a check failed. The installed check is `/usr/lib/setup-system/ansible/files/editor_check.lua`. [Recovery](recovery.md)

## ⌨️ Edit a project

Open Neovim in a project. Use `<leader>ff` for files, `<leader>fg` for search, `-` for Oil and `<leader>cf` to format explicitly. Both search commands use ripgrep and the editor catalog’s `[search].exclude_globs`, which filters out credentials, backups and dependency directories.

The profile sets `EDITOR`, `VISUAL` and `GIT_EDITOR` to `nvim` through the user environment configuration. A new login picks up those values. Optional nano removal is a separate plan that checks the selected editor and requires another installed administrative editor. [Terminal and shell](terminal_guide.md)
