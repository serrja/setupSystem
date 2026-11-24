# Debian workstation target

Open a terminal as your desktop user. If the Debian package is installed, run this command from any directory.

```sh
setup-system
```

Choose **Base system** for Debian, Hyprland, graphical login and regional settings. Choose **Working environment** for the theme, applications, editor and projects. **Inspect** reads the current machine. Review a plan and confirm **Apply** when you want to change it. Press **F1** for the installed guides or run `man setup-system` in a terminal. [Using the configurator](textual_workflow.md) · [Settings](configuration.md) · [Recovery](recovery.md)

## 📦 Install on Debian

Use an existing Debian 13 (trixie) installation on amd64 or arm64 with configured APT sources and a normal user account. HTTPS delivery requires a POSIX shell, curl with CA certificates, jq, dpkg, APT, util-linux, coreutils and the GitHub CLI. Its `gh attestation verify` command must support bundle, source and signer verification. The script checks the required flags. The download works without a checkout, Make, Python or uv.

Download the delivery script as your normal user and run its `install` action. It resolves the latest stable release once, checks the package checksum and GitHub build attestation, then requests authorization for APT. Installation ends after the configurator and its runtime dependencies are in place.

```sh
( set -eu
  delivery=$(mktemp)
  trap 'rm -f -- "$delivery"' EXIT
  curl -qfsSL --proto '=https' --proto-redir '=https' \
    https://raw.githubusercontent.com/serrja/setupSystem/main/install.sh -o "$delivery"
  /bin/sh "$delivery" install
)
```

If only the local root account can authorize installation, replace the last command inside the parentheses with `/bin/sh "$delivery" install --auth su`. Native `su` will ask for the root password. For the usual sudo path, use the installing user's sudo password. [Administrative access](administration.md)

## 🔄 Update the configurator

Run the same delivery script with `update` to verify and install the latest stable package. The update ends at the APT transaction. Open the app yourself when you are ready.

```sh
( set -eu
  delivery=$(mktemp)
  trap 'rm -f -- "$delivery"' EXIT
  curl -qfsSL --proto '=https' --proto-redir '=https' \
    https://raw.githubusercontent.com/serrja/setupSystem/main/install.sh -o "$delivery"
  /bin/sh "$delivery" update
)
```

## 🗑️ Remove the configurator

```sh
sudo apt remove setup-system
```

APT removes the configurator. The workstation applications, user settings and backups stay on disk. To restore managed files, preview **Restore** in the app before removing it. [Recovery](recovery.md)

## 🖥️ Inspect the target

System changes require a matching distribution, release, codename and architecture. The configurator works with the existing filesystems, boot loader and account. [Linux execution boundaries](linux.md)

## System packages

Selecting **Hyprland, sound, portals and desktop dependencies** includes the desktop package set. Graphical login adds greetd, gtkgreet and Cage. The release lists the package names and which ones come from trixie-backports. APT selects candidates from signed Debian stable, updates, security and backports indexes. The installed catalog and source definitions are `packages.toml` and `debian.sources` under `/usr/lib/setup-system/config/base`.

**Plan** refreshes indexes in a private run directory and simulates the complete transaction. Inspect the package changes before confirming. For a system change, the app then asks for the local authorization password. Apply simulates again and compares the intended actions and versions with the package operations APT supplies immediately before dpkg. Package installation can leave completed changes after a failure. Check the application's result and Debian's package logs before retrying. [Recovery](recovery.md)

## Regional settings

Timezone, locale and keyboard each have their own field. An empty field preserves the corresponding system value. **Inspect** refreshes suggestions from installed zoneinfo, Debian's UTF-8 locale list and XKB rules. The plan compares each selection with the active system and includes any required Debian packages.

Locale generation retains existing selections. The keyboard operation preserves unrelated entries in `/etc/default/keyboard` and uses `setupcon --keyboard-only --save-only` to prepare the console keymap. An updated console layout takes effect in subsequent sessions. Each operation verifies its result and stores recovery evidence under `/var/lib/setup-system/regional/`.
