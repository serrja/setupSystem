# Contributing to setupSystem

setupSystem owns Debian installation and managed user configuration. Change upstream
templates and policy in their owning repositories; this consumer accepts reviewed
immutable pins. Preserve public CLI identifiers, user files, projectRules and recovery
contracts. Read [AGENTS](AGENTS.md) and the pinned baseRules contract before editing.

## 🧪 Prepare and check

Use the exact Git root, Git, Make and uv 0.12.15. The native development toolchain uses
Python 3.14.7; the CLI also checks its declared Python 3.12 minimum in CI.

```bash
uv python install 3.14.7
uv sync --locked --all-groups --python 3.14.7
make native-check
make native-ci
```

`native-check` runs locked formatting/lint, types, compilation, lock validation,
read-only bootstrap checks and fixture tests. `native-ci` adds wheel/sdist builds and
installation into a disposable environment outside the checkout. No gate installs
workstation packages or changes the developer HOME. Docker `make ci-local` is an
alternative full gate with source/artifact receipts; see [development](docs/development.md).
Record actual failed, unsupported and unavailable checks in the PR.

## 🤝 Review and delivery

Create one lowerCamelCase working branch from fresh `origin/test`, for example
`refactorG03HyprlandSetup`. Preserve the owner's main checkout and dirty files; use a
separate worktree when needed. Use English Conventional Commits such as
`fix(bootstrap): validate the sudo account before apply` and review staged paths,
complete diffs, secrets and exact source SHA before committing.

Open the working PR against `test`; promote `test` to `dev`, then `dev` to `main` with
ancestry-preserving merges. The automatic contribution guard rejects other promotion
paths. Review actual checks and configured server requirements; never bypass them or
delete/force-update the permanent branches. Branch-protection administration is not an
installer requirement. [Release recovery](docs/release_guide.md) uses the original
workflow event and never moves tags or republishes conflicting assets.

Report a security issue privately to the repository owner. Do not include credentials,
private HOME paths, real user data or a working exploit against someone else's system.
