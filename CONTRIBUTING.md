# Contributing to setupSystem

setupSystem configures Debian workstations and manages selected user files. Keep published package and entrypoint names, user data, projectRules and recovery contracts compatible. Submit template and policy changes to their owning repositories, then update the reviewed pins here. Read the project rules and pinned baseRules contract before editing. [AGENTS.md](AGENTS.md)

## 🧪 Prepare and check

Use Git, Make and uv 0.12.15 from the repository root. The development toolchain uses Python 3.14.7. CI also checks the supported minimum of Python 3.12.

```sh
uv python install 3.14.7
uv sync --locked --all-groups --python 3.14.7
make native-check
make native-ci
```

`native-check` runs the locked formatter, linter, type checker, lock and entrypoint checks, and fixture tests. `native-ci` also builds the wheel and source archive, compares their runtime resources and installs the wheel in a temporary environment outside the checkout. Run `make package` from the repository root to build the Debian archive from the tracked Git content. Record failed, skipped and unavailable checks in the PR. [Development](docs/development.md) · [Disposable Debian tests](docs/testing.md)

Add a configuration field and regenerate its offline reference using the [configuration development workflow](docs/development.md#change-a-setting). Exercise the field through its consuming interface.

## 🤝 Review and delivery

1. Create a lowerCamelCase working branch from fresh `origin/test`, for example `refactorG03HyprlandSetup`. A separate worktree keeps the owner's checkout and local edits intact.
2. Review the complete diff, staged paths, source SHA and any accidental secrets. Use English Conventional Commits such as `fix(installer): validate the sudo account before apply`.
3. Open a PR into `test`. Promote `test` to `dev` and then `dev` to `main` with ancestry-preserving merges. The contribution guard checks this route. Honor configured reviews and server checks, and preserve all three permanent branches.
4. For a failed publication, recover from the original workflow event and retain the existing tags and matching assets. [Release recovery](docs/release_guide.md)

Report security issues privately to the repository owner. Keep credentials, private HOME paths, real user data and working exploits against other systems out of reports.
