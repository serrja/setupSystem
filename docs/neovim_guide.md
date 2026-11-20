# Neovim and language profiles

Under **Working environment**, select **Neovim** to install the pinned upstream editor,
lazy.nvim, blink.cmp, LSP configuration, conform.nvim, Tree-sitter, fzf-lua, Oil and
Git navigation. setupSystem takes release digests, plugin revisions and parser selections
from `manifests/editor.toml`. It generates the native Lazy lock when writing the user's
configuration. Startup works offline. Changing a profile requires another confirmed Apply.

The editor uses the Wayland clipboard in a Wayland session. Open it from the project
directory to enable language servers and explicit formatting with `<leader>cf`.
Python projects can provide `.venv/bin/python`. Prettier requires project formatting
settings. Modelines and project-local Lua configuration are disabled.

| Selected profile | What Apply uses |
| --- | --- |
| Python | A dedicated hash-locked environment for Ruff and basedpyright |
| TypeScript and Vue | Node from Debian and a release npm lock for TypeScript, the TypeScript and Vue language servers, the Vue TS plugin and Prettier |
| Dart and Flutter | An existing Flutter SDK selected by an absolute `FLUTTER_ROOT` or found on PATH, or the reviewed official Linux x86_64 Flutter SDK with its bundled Dart language server and formatter. Downloading and unpacking the Flutter distribution requires several GiB of free space. |
| C++ | Debian clangd and clang-format |

With both the editor and a language selected, Apply builds missing language parsers at
the revisions specified by the pinned Tree-sitter plugin. It then starts Neovim from the
absolute managed init path in a temporary project. Each selected parser has to parse a
buffer, each LSP has to initialize, and each formatter has to format a sample. A second
headless run disables parser installation. Without a language selection the editor checks
the parsers bundled by Neovim. The release contains a Tree-sitter CLI pin for each
supported architecture.

After verification, setupSystem writes the environment.d settings for `EDITOR`,
`VISUAL` and `GIT_EDITOR`. The user session picks those up at the next login. Nano
removal has a separate confirmation and checks the administrative fallback editor.
