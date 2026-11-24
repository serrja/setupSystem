# Workspace projects

Use **Working environment** to inspect projects, prepare owner directories or open a verified checkout in Neovim or Codex. The project catalog at `/usr/lib/setup-system/config/workspace/projects.toml` contains the canonical owners, project IDs, template aliases and origins, including `pokerlost/projectRules`. The default root is `$HOME/Projects`.

A project must be an exact Git working tree with the expected effective fetch and push origin. The picker preserves existing files, local commits, refs and dirty changes. Preparing the catalog directories does not clone or initialize repositories. Template creation and updates have their own previews. [Project templates](templates.md).
