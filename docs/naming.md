# Naming policy

Owned Python follows PEP 8: modules, functions, methods, parameters, variables, and internal fields use `snake_case`; classes use `CapWords`; constants use `UPPER_SNAKE_CASE`. New Python code uses Ruff's formatter and targeted naming checks. Public docstrings explain contracts consistently with PEP 257. Existing code is migrated with its callers and tests, not by changing a published protocol in isolation.

Repository names such as `pythonTemplate`, current distribution names, stable CLI commands, Copier answers, environment variables, serialized fields, HTTP paths, OpenAPI operation IDs, and required tool filenames remain external contracts. Keep their spelling unless a deliberate compatibility migration is reviewed. Other languages follow their own project conventions.

The historical `projectTemplates` to `pythonTemplate` repository rename is complete. Existing pinned commits and the old repository redirect remain compatibility inputs; do not recreate the repository or rewrite that history.
