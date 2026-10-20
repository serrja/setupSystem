# Versioning

`pyproject.toml` `[project].version` is the canonical application version, using
PEP 440. Installed distribution metadata and built wheel/sdist metadata must
match it; `make version` reads the same source and `make release-check` validates
consistency without publishing. The catalog's `schemaVersion` describes its
serialization contract, not the application's release number.

Compatible corrections use a patch increment; new compatible CLI features use a
minor increment. During 0.x development, an incompatible public change also
requires a minor increment and an explicit migration note. After 1.0, incompatible
changes require a major increment. Development candidates use `.devN`, release
candidates use `rcN`; do not overwrite a published artifact or tag. A plain
`v<PEP-440-version>` annotated tag identifies an exact reviewed main commit.

The versioned templates CLI is a new minor development candidate. Existing
`templates list/path`, project IDs and checkout paths remain supported. Optional
template metadata extends catalog schema 1; the application version signals the
new reader and commands. The template's version and generated application's
version remain separate. Reviewed source tags and full commits live together in
the catalog; changing `recommendedVersion` is an explicit reviewable update.

Before a release, review CLI spelling and exits, Python imports, catalog/config
compatibility, generated answers and user-owned files, bundled resources,
Python's minimum runtime and Debian installer support. This feature preserves
Python 3.12+ for the CLI and the existing Debian 12/13 installer boundary.
