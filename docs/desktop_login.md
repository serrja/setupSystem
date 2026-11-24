# Graphical login

In **Base system**, select **Hyprland, sound, portals and desktop dependencies** and **Graphical login**. Inspect the plan before confirming. It includes the user session launcher and, when required, greetd, gtkgreet and Cage. The greeter runs as the dedicated `_greetd` account. PAM authenticates the desktop user. A system-owned bridge starts Hyprland with that user's HOME and XDG directories.

Apply checks the same account, launcher, display-manager state, default target and GRUB inputs captured during planning. It backs up managed files before writing them and validates the generated GRUB configuration. The managed drop-in keeps a visible five-second boot menu and a ten-second recovery timeout after a failed boot. Enabling graphical login sets the default for subsequent boots. The current session stays open. An encrypted HOME must already be accessible for user configuration. [Graphical disk unlock](graphical_unlock.md)

## Recovery and verification

If the greeter fails, switch to a text console with Ctrl+Alt+F3. These commands show the service state and messages from the current boot.

```sh
systemctl status greetd
journalctl -b -u greetd
```

The login journal at `/var/lib/setup-system/desktop-backups/operation.json` records previous state and managed paths. Check the journal and its backups before restoring files or changing the default target. [Recovery](recovery.md)

On an approved disposable target, test failed and successful authentication, the normal-user compositor, text-console access and the site's existing remote access. Keep a working console or rescue entry until those checks pass.
