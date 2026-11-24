# Workspace projects

Open **Working environment** to inspect projects, prepare their parent directories or launch Neovim or Codex in a verified checkout. The project catalog at `/usr/lib/setup-system/config/workspace/projects.toml` contains the canonical owners, project IDs, template aliases and origins, including `pokerlost/projectRules`. The default root is `$HOME/Projects`.

## 📂 Select a checkout

A project must be an exact Git working tree with the expected effective fetch and push origin. The picker preserves existing files, local commits, refs and dirty changes. Preparing the catalog directories does not clone or initialize repositories. Template creation and updates have their own previews. [Project templates](templates.md).
