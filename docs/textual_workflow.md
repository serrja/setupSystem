# Textual workflow

Run `setup-system` as your desktop user. You can switch between **Base system** and **Working environment** while editing or while an operation runs. Inspect refreshes local facts and suggestions. Save writes the current section to its XDG configuration. Reload asks before discarding unsaved edits in that section.

## ✅ Confirm and apply a selection

Preview shows which paths, packages and settings your selection would change. The Apply button and confirmation identify the section that produced the preview, including when you switch tabs. Enter the become password only when the confirmed operation needs system tasks. Editing a form while a run is active changes the next preview. An external edit to a saved configuration invalidates its pending confirmation.

When you reopen the app, it shows the last recorded operation and its status. For a partial or unknown outcome, inspect the current state before preparing another plan. Cancellation stops at a safe boundary. Leave a running package transaction time to finish. Preview a recovery operation to see which managed files can be restored. [Configuration](configuration.md) and [recovery](recovery.md).
