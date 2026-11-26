# setupSystem

Read the pinned [baseRules engineering contract](https://github.com/serrja/baseRules/blob/8434509933feaa71ba312f8d02caad7362766d5a/docs/engineering.md) and [policy](https://github.com/serrja/baseRules/blob/8434509933feaa71ba312f8d02caad7362766d5a/docs/policy.md). Follow [naming](docs/naming.md). The engineering upstream is pythonTemplate.

Textual is the public configuration interface. The normal-user application invokes fixed Make targets. The Debian package owns the executable playbooks and resources. Use native Ansible become for individual system tasks. Validate the machine, source and confirmed snapshot again at the privilege boundary. Keep Preview read-only and preserve managed-file recovery, projectRules, user data and local changes.

Load host and database preferences through `setup_system.settings.Settings`. Store one revision-checked TOML document per extension. Plugin packages own their action and settings models, resource declarations, native recipes and Help. Keep editable intentions, package-owned catalogs, privileged policy and observed facts in their respective contracts. Regenerate the offline [settings reference](docs/settings_map.md) and each packaged plugin reference when their source models change.

Package installation and update end at APT. Opening the application, Preview, explicit Apply and Restore have separate entry points and confirmation rules. Use isolated HOME/XDG directories for development diagnostics. The owner has withdrawn the concrete product test suite. Preserve pytest fixtures, static checks, schema migration and package integrity checks. Use a separately authorized disposable Debian machine for operating-system, hardware and boot acceptance.

Write public engineering prose and comments in English. Run the [locked native gate](docs/development.md) and record isolated manual observations separately. Promote a reviewed lowerCamelCase branch through `test`, `dev` and `main` with ancestry-preserving merges. Preserve permanent branches and immutable releases. Follow [release and recovery](docs/release_guide.md).
