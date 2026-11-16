# Workspace catalog

`manifests/projects.toml` is the sole catalog for canonical `$HOME/Projects/OWNER/REPO`
locations. Entries grant no filesystem permissions and do not create repositories.
Only exact Git roots are discovered; nested directories and symlink traversal are rejected.
Explicit custom project roots also verify all effective fetch/push origins.

```bash
setup-system projects list
setup-system projects path --name serrja/setupSystem
setup-system projects open --name serrja/setupSystem
setup-system projects open --name serrja/setupSystem --tmux
```

The catalog contains current accessible serrja/pokerlost repositories, including
projectRules and pokerDemo. It does not list unavailable jobdesk or foreign-owner
aliases. Repository availability is an audit-time observation, not an instruction to
fetch on every launch or delete local user data.

`projects prepare --yes` creates only missing owner directories on supported Debian.
It never initializes repositories. Old checkout relocation commands and path aliases
have been removed; existing data outside canonical locations is left untouched.

Templates use the same reviewed catalog and immutable release pins. See [templates](templates.md).
`agents launch serrja/setupSystem --mode review` resolves the discovered root at launch.
User-owned scopes remain available for unrelated names and are not rewritten by the picker.
