# setupSystem

Read the pinned [baseRules engineering contract](https://github.com/serrja/baseRules/blob/8434509933feaa71ba312f8d02caad7362766d5a/docs/engineering.md) and [policy](https://github.com/serrja/baseRules/blob/8434509933feaa71ba312f8d02caad7362766d5a/docs/policy.md). Follow [naming](docs/naming.md). The engineering upstream is pythonTemplate.

Textual is the public configuration interface. The normal-user application invokes fixed Make targets. The Debian package owns the executable playbooks and resources. Use native Ansible become for individual system tasks. Validate the machine, source and confirmed snapshot again at the privilege boundary. Keep Preview read-only and preserve managed-file recovery, projectRules, user data and local changes.

Load application and stage settings through `setup_system.configuration.Settings`. Keep editable intentions, package-owned catalogs, privileged policy and observed facts in their respective contracts. Regenerate the offline [settings reference](docs/settings_map.md) when its source models change.

Package installation and update end at APT. Opening the application, Preview, explicit Apply and Restore have separate entry points and confirmation rules. Run all development tests in isolated directories. Use a separately authorized disposable Debian machine for operating-system, hardware and boot acceptance.

Write public engineering prose and comments in English. Run the [locked native gate](docs/development.md) and relevant integration tests. Promote a reviewed lowerCamelCase branch through `test`, `dev` and `main` with ancestry-preserving merges. Preserve permanent branches and immutable releases. Follow [release and recovery](docs/release_guide.md).
