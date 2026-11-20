# Project templates

Open `setup-system`, choose Workspace → Tools → templates to inspect reviewed catalog
versions. Select create_template or update_template, an exact catalog alias, an explicit
destination and optionally a reviewed version. Copier answers are a JSON object in the
Data field. Confirm the operation inside Textual.

The catalog retains Python, Typer, FastAPI and service templates as products. setupSystem's
own engineering scaffold derives exclusively from PythonTemplate. `projectRules` remains
an active repository catalog entry; see [workspace](workspace_guide.md).

Git tags `vX.Y.Z-dev.N` map explicitly to Python versions `X.Y.Z.devN`; existing accepted
PEP 440 tags remain compatible. Only reviewed immutable commits and annotated tags are
accepted. template_cache prepares the selected verified source for offline use. Missing
cache is a failure, not permission to fetch an unrelated mutable ref.

Create requires a new/empty destination. Update requires recorded trusted Copier answers
and a clean exact Git root. Preserve project-owned files, branches and history. Internal
Copier answer keys, unreviewed URLs, symlink destinations and template tasks are rejected.
Review merge conflicts manually; they never become a success result. Commit generated
projects through their own normal review workflow, outside this installer operation.
