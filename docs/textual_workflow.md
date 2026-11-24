# Textual workflow

Run `setup-system` as your desktop user. You can switch between **Base system** and **Working environment** while editing or while an operation runs. Inspect refreshes local facts and suggestions. Saving a section updates its XDG configuration.

## ✅ Confirm and apply a selection

Preview shows which paths, packages and settings your selection would change. Apply uses that preview after confirmation. Enter the become password only when the confirmed operation needs system tasks. Editing a form while a run is active changes the next preview. An external edit to a saved configuration invalidates its pending confirmation.

Cancellation stops at a safe boundary. Leave a running package transaction time to finish. Preview a recovery operation to see which managed files can be restored. [Configuration](configuration.md) and [recovery](recovery.md).
