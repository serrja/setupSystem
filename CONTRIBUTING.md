# 🛠️ Contributing to setupSystem

Keep the published distribution and entry-point names, settings and recovery data compatible. Read the repository guidance and its pinned engineering contract. [AGENTS.md](AGENTS.md)

## 🧪 Test environment

The host and SDK use the locked Python workspace. Run the checks in a private development environment.

```sh
make prepare
make check
make check-installed
make plugin-types
make plugins-check
```

Plugin authors maintain their manifests and packaged reference pages from their action and settings models. Record the checks and outstanding target-specific acceptance in the pull request. [Development](docs/development.md)

## 📦 Build and releases

1. Create a lowerCamelCase working branch from fresh `origin/test`, for example `refactorG03HyprlandSetup`. A separate worktree keeps the owner's checkout and local edits intact.
2. Review the complete diff, staged paths, source SHA and any accidental secrets. Use English Conventional Commits such as `fix(installer): validate the sudo account before apply`.
3. Open a PR into `test`. Promote `test` to `dev` and then `dev` to `main` with ancestry-preserving merges. The contribution guard checks this route. Honor configured reviews and server checks, and preserve all three permanent branches.
4. For a failed publication, recover from the original workflow event and retain the existing tags and matching assets. [Release recovery](docs/release_guide.md)

Report security issues privately to the repository owner. Keep credentials, private HOME paths, real user data and working exploits against other systems out of reports.
