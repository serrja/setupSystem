# Workspace catalog

Working environment → Tools → Projects lists exact local checkouts from the repository catalog. Select a checkout to open Neovim or Codex in that directory. Prepare project directories creates the canonical owner directories. The shell's `cproj` helper opens the Textual application.

The default owner roots are `$HOME/Projects/serrja` and `$HOME/Projects/pokerlost`.
`manifests/projects.toml` owns project IDs, origins and reviewed policy/template metadata.
An explicit custom project root must still validate the exact Git root and effective
origin. Dirty working trees, alternate HOME paths and paths with spaces remain supported.
No obsolete owner spelling is selected automatically.

`pokerlost/projectRules` remains active with its
[reviewed immutable policy](https://github.com/pokerlost/projectRules/blob/537da92f8e22f35355a96f868d1b98d83e066c75/docs/project.md).
Its catalog digest and origin must match; a parent Git repository does not establish a
valid nested checkout. Catalog data never grants write access to another repository.
See [templates](templates.md) and [Git profiles](git_profiles.md).
