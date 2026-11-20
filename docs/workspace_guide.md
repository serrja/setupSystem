# Workspace catalog

Open `setup-system` → Workspace → Tools. projects lists discovered exact checkouts;
prepare_projects creates only canonical owner directories. open_project requires an exact
active catalog name and opens the normal user's editor in that root. It never initializes
or resets a repository. The shell's `cproj` helper opens the same Textual application.

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
