# Testing and delivery

Git, Make and Docker suffice from this exact root:

```bash
make check
make coverage
make build
make verify
make image
```

`check` runs locked Ruff, strict mypy, compilation, bootstrap help/plan and all 71 tests once.
`coverage` reports the fresh result without rerunning tests. The collector covers the whole
`setup_system` package and stdlib bootstrap, including unimported modules. Statements and
branches are separate; below 100% warns. Assertions, types, builds and missing/corrupt/stale
reports fail. Untested installer/application branches remain visible without exclusions.

Tests use synthetic temporary homes, Git repositories and files. Backup/recovery checks assert
private permissions, hashes, conflict preservation and symlink/traversal rejection. Catalog
loading does not create directories or initialize Git. No source check applies workstation
settings, invokes sudo, mounts the user HOME/socket, relocates projects or repeats Zsh/RTK.

`build` exports the canonical wheel/sdist. `verify` runs the installed CLI outside the source
checkout and verifies its metadata and bundled platform layout resources. `image` creates a
local non-root CLI candidate and never publishes. CI uses the same pinned Debian Docker graph
and retains reports/distributions for 14 days. This does not establish another OS or GUI target.
