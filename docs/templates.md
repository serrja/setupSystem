# Project templates

Open Working environment → Tools → Templates. Choose a template and a reviewed version, enter the project directory and provide a name when creating a project. For Python, choose a library, service or native profile. Select Create project or Update existing project, then review the confirmation.

The catalog retains Python, Typer, FastAPI and service templates as products. setupSystem's
own engineering scaffold derives exclusively from PythonTemplate. `projectRules` remains
an active repository catalog entry; see [workspace](workspace_guide.md).

Git tags `vX.Y.Z-dev.N` map explicitly to Python versions `X.Y.Z.devN`; existing accepted
PEP 440 tags remain compatible. Only reviewed immutable commits and annotated tags are
accepted. Prepare offline template caches the selected verified source for offline use. Missing
cache is a failure, not permission to fetch an unrelated mutable ref.

Create requires a new/empty destination. Update requires recorded trusted Copier answers
and a clean exact Git root. Preserve project-owned files, branches and history. Internal
Copier answer keys, unreviewed URLs, symlink destinations and template tasks are rejected.
Review merge conflicts manually; they never become a success result. Commit generated
projects through their own normal review workflow, outside this installer operation.
