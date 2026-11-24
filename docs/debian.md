# Debian workstation target

Open `setup-system` as the owner of an existing Debian 13 (trixie) installation on amd64 or arm64. In **Base system**, use **Inspect** to read the machine's capabilities. A different distribution, release, codename or architecture blocks system changes. The configurator starts with the existing filesystems, boot loader and account. [Linux execution boundaries](linux.md)

## System packages

Selecting **Hyprland, sound, portals and desktop dependencies** includes the desktop package set. Graphical login adds greetd, gtkgreet and Cage. The release lists the package names and which ones come from trixie-backports. APT selects candidates from signed Debian stable, updates, security and backports indexes. [Package selections](../config/base/packages.toml) · [Debian sources](../config/base/debian.sources)

**Plan** refreshes indexes in a private run directory and simulates the complete transaction. Inspect the package changes before confirming. For a system change, the app then asks for the local authorization password. Apply simulates again and compares the intended actions and versions with the package operations APT supplies immediately before dpkg. Package installation can leave completed changes after a failure. Check the application's result and Debian's package logs before retrying. [Recovery](recovery.md)

## Regional settings

Timezone, locale and keyboard each have their own field. An empty field preserves the corresponding system value. **Inspect** refreshes suggestions from installed zoneinfo, Debian's UTF-8 locale list and XKB rules. The plan compares each selection with the active system and includes any required Debian packages.

Locale generation retains existing selections. The keyboard operation preserves unrelated entries in `/etc/default/keyboard` and uses `setupcon --keyboard-only --save-only` to prepare the console keymap. An updated console layout takes effect in subsequent sessions. Each operation verifies its result and stores recovery evidence under `/var/lib/setup-system/regional/`.
