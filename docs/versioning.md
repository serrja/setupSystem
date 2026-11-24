# Versioning

`pyproject.toml` `[project].version` is the canonical PEP 440 version floor.
The catalog's `schemaVersion`, template versions and generated application versions
are separate contracts. Native source builds keep the declared version; installed
wheel/sdist metadata must match it.

Use a patch increment for compatible corrections and a minor increment for compatible
CLI features. During 0.x, document migrations for incompatible public changes and
increment the minor version. After 1.0, such changes require a major increment. Review CLI spelling/exits, imports,
configuration, owner data, resources, Python minimum and Debian profile capabilities.

## 🏷️ Release mapping

GitHub dev tags use `vX.Y.Z-dev.N`. The same release uses `X.Y.Z.devN` in Python metadata.
Main tags use `vX.Y.Z` with native metadata `X.Y.Z`. Convert with a version parser,
not string ordering; dev number 10 follows 9. Existing historical `.devN` tags and
accepted template tags remain immutable compatibility inputs.

The pinned H02 provider allocates a version once per original successful dev/main
push event, above existing accepted release history and the source floor. Every
push, including docs-only, enters that DAG. The native builder exports the exact
clean event SHA, injects the allocated Python version using
`uv version --frozen --offline`, and builds new metadata without editing the
checkout or lockfile. Renaming an archive is not version injection.

The provider independently verifies the declared backend, wheel/sdist metadata,
RECORD/checksums, source identity and attestations before publication. Tags,
allocation and build records are append-only. [Release recovery](release_guide.md)
reuses the original event, version and verified build; it never moves an old tag.
