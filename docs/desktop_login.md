# Graphical login

In **Base system**, select **Hyprland, sound, portals and desktop dependencies** and **Graphical login**. Review the login and boot changes before confirming. The plan includes the user session launcher and, when required, greetd, gtkgreet and Cage. The greeter runs as the dedicated `_greetd` account. PAM authenticates the desktop user. A system-owned bridge starts Hyprland with that user's HOME and XDG directories.

## 🖥️ Enable graphical login on the next boot

Apply checks the same account, launcher, display-manager state, default target and GRUB inputs captured during planning. It backs up managed files before writing them and validates the generated GRUB configuration. The managed drop-in keeps a visible five-second boot menu and a ten-second recovery timeout after a failed boot. Enabling graphical login sets the default for subsequent boots. The current session stays open. An encrypted HOME must already be accessible for user configuration. [Graphical disk unlock](graphical_unlock.md)

## Session lock

The greeter accepts the local account for a new login. The system-owned session launcher checks which account is starting Hyprland. Inside an existing session Hyprlock shows its current owner and authenticates the unlock through the native PAM stack. Both native views use the same Mocha Green background, font, card, input dimensions, spacing and color roles. The login error label carries the shared error border. gtkgreet also sets its own red message text. Hyprlock uses the shared error color on the input field. Workspace theme changes affect personal files. Updating the system greeter requires a confirmed Base change. [gtkgreet styling](https://manpages.debian.org/trixie/gtkgreet/gtkgreet.1.en.html) · [Hyprlock configuration](https://wiki.hypr.land/hypr-ecosystem/user/hyprlock/)

## Recovery and verification

If the greeter fails, switch to a text console with Ctrl+Alt+F3. These commands show the service state and messages from the current boot.

```sh
systemctl status greetd
journalctl -b -u greetd
```

The login journal at `/var/lib/setup-system/desktop-backups/operation.json` records previous state and managed paths. Check the journal and its backups before restoring files or changing the default target. [Recovery](recovery.md)

On an approved disposable target, test failed and successful authentication, the normal-user compositor, text-console access and the site's existing remote access. Keep a working console or rescue entry until those checks pass.
