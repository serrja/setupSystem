# Linux execution boundaries

Run `setup-system` in an interactive terminal as the normal workstation account. The two Textual sections share one active operation. Use **Inspect** to read local facts. A plan can query release metadata, simulate packages and render files in a temporary run directory. **Save** writes the selected section's TOML settings. [Configuration](configuration.md)

## 🔐 When a task needs root

System changes require the installed Debian package. After confirmation, the app passes a one-use password to Ansible's hidden local prompt. Debian supplies the system interpreter and Ansible. The installed recipes and helpers live under `/usr/lib/setup-system`. Only designated system tasks request root. Personal files and editor, shell and project configuration run under the workstation account.

Use the result and the affected paths when assessing a failed or interrupted change. A verified file backup can restore a managed configuration. Debian package transactions and boot-loader changes have different recovery procedures. [Debian target](debian.md) · [Recovery](recovery.md) · [Architecture](architecture.md)
