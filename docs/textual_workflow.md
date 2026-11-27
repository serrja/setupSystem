# 🧩 Using the configurator

Open `setup-system` in a terminal as the desktop user. The catalog shows reviewed releases and activated extensions; it does not install workstation applications automatically.

## 🧩 Plugin states and actions

**Available** means a release descriptor and its wheel catalog can be reviewed. **Activating** covers wheel loading, uv verification, environment creation and backend validation. **Active** means the extension generation passed validation and is selected. A provider-only extension shows its capabilities and active-provider status without empty Settings or action forms. An installed `.deb`, an active extension and an installed workstation application are separate facts.

A plugin page shows Settings only when it declares fields, Save only when a field can be edited, and Actions only when it declares actions. Accounts actions come from the same validated manifest. An unsupported control is absent. A temporarily blocked action remains visible with its missing capability and compatible provider candidates. Select and activate that provider explicitly before previewing again.

Read-only actions run without Apply. Manual actions show instructions for an official client. Mutations require Preview, a confirmed plan and Apply. A `NO_CHANGES` plan without steps has no Apply action. Operation progress remains bound to the runtime operation ID if you switch pages. Cancel requests use that ID and wait for the executor's safe boundary. [Architecture](architecture.md) · [Recovery](recovery.md)

## ⚠️ Errors and next steps

The CLI prints one `Error [code]: message` line and an optional `Next:` line. The plugin page keeps a short error beside the action or invalid field. Click **Details** to see the diagnostic ID and private journal path. Diagnostics omit settings, secrets, raw stderr and exception values.

| Code | Safe next step |
| --- | --- |
| `configuration_invalid` | Correct the indicated setting or start the app in an interactive terminal. |
| `database_needs_migration` | Review the private database, then run `setup-system migrate`. |
| `storage_unavailable` | Check the owner, permissions and free space of the private XDG state directory. |
| `download_failed` | Retry when the approved HTTPS release or bundled uv source is reachable. |
| `integrity_mismatch` | Review the release source and independently approved SHA-256. |
| `dependency_missing` | Activate a release with a complete reviewed plugin closure. |
| `activation_failed` | Review the reported activation stage and selected release. |
| `capability_missing` | Activate or select a compatible provider in the catalog. |
| `native_executor_unavailable` | Review the installed Debian companion and its executor record. |
| `internal_error` | Use the diagnostic ID to inspect the private failure record. |

An unknown outcome requires readback and recovery review before retrying. A successful backend import validates only import and metadata; it does not prove that every inspect, plan, native Apply or boot change works. Machine and boot acceptance need an authorized disposable Debian target. [Plugin trust](plugin_trust.md) · [Test environment](testing.md)
