# Textual workflow

Open `setup-system` as the desktop user. Switch between **Base system** and **Working environment** while editing or during a run. Inspect refreshes local facts and suggestions. Saving a section updates its XDG configuration.

Preview checks the selected changes and shows their effects. Apply uses that preview after confirmation. Enter the become password only when the confirmed operation needs system tasks. Editing a form while a run is active changes the next preview. An external edit to a saved configuration invalidates its pending confirmation.

Cancellation stops at a safe boundary. Leave a running package transaction time to finish. Preview a recovery operation to see which managed files can be restored. [Configuration](configuration.md) and [recovery](recovery.md).
