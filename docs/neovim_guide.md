# Neovim

Select Neovim in Textual's Working environment. Existing lazy.nvim, blink.cmp,
LSP, conform.nvim, Tree-sitter, fzf-lua, oil.nvim, Git navigation and project-root
selection remain pinned in `manifests/terminalDev.toml` and `assets/nvim/lazy-lock.json`.
No LazyVim/AstroNvim layer or extra AI plugin is added.

The maintained Catppuccin plugin is pinned. A native Mocha Green fallback works before
plugin/network availability; both take colors from the shared palette.
No startup clone, plugin update or automatic parser installation runs on editor launch.
Wayland clipboard uses the normal wl-clipboard provider. Other sessions retain a local
clipboard fallback. Per-project formatters and `.venv` remain authoritative.

Language tool installation is a separate explicit selection; Python uses a hash-locked
Ruff and one basedpyright LSP in its own user environment. Unverified TS/Vue, Dart/Flutter or C++ delivery must remain blocked,
not installed as a side effect of opening Neovim. Existing detection can use tools that
are already present.

Verify the installed editor, EDITOR/VISUAL and a fallback before selecting Nano removal.
The cleanup adapter refuses unverified editor replacement and unexpected APT dependencies.
