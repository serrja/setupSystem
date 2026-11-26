# setupSystem

Read the pinned [baseRules engineering contract](https://github.com/serrja/baseRules/blob/8434509933feaa71ba312f8d02caad7362766d5a/docs/engineering.md) and [policy](https://github.com/serrja/baseRules/blob/8434509933feaa71ba312f8d02caad7362766d5a/docs/policy.md). Follow [naming](docs/naming.md). The engineering upstream is pythonTemplate.

Textual is the public configuration interface. Keep Preview read-only. The host owns the operation ledger, confirmation, provider selection and generation leases. Run plugin handlers in their selected worker environment. Keep platform code, native resources, packaging, tests and CI under the owning flat plugin directory.

Load host preferences through `setup_system.configuration.settings.Settings`. Store one revision-checked TOML document per plugin. Each plugin owns its settings and action models, manifest, catalogs and Help. Regenerate its manifest and reference when those models change. Preserve projectRules, local modifications, managed-file recovery evidence and user data.

Use isolated HOME and XDG directories, real subprocesses for cancellation and PTY checks, and synthetic databases for migrations. Run the [development checks](docs/development.md). Machine, hardware and boot acceptance require a separately authorized disposable target.

Write public engineering prose and comments in English. Use snake_case for Python names and CapWords for classes. Promote a reviewed lowerCamelCase branch through `test`, `dev` and `main` with ancestry-preserving merges. Preserve permanent branches and immutable releases. Follow the [release guide](docs/release_guide.md).
