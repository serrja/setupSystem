# setupSystem

Configure an existing Debian 13 (trixie) workstation on amd64 or arm64. The **Base system** tab handles Debian, Hyprland, regional settings and graphical login. The **Working environment** tab handles applications, the Catppuccin Mocha Green profile, Neovim, language tools and managed files.

## Install the configurator

Use a normal account with access to sudo and configured Debian package sources. Remote delivery requires a POSIX shell, curl with CA certificates, jq, dpkg, APT, util-linux, coreutils and a GitHub CLI that supports signed build attestations with source and signer constraints. The delivery script checks the required flags. Downloading the package requires neither a checkout nor Make, uv or Python.

The following command is for stable versions that publish a Debian companion package. Check the release for its matching Debian companion before installation. If you used the Python-only delivery from 0.4.x, use the install command below to switch to the system package. Existing section settings migrate on Save. Keep your configuration and recovery files.

```sh
( set -eu; delivery=$(mktemp); trap 'rm -f -- "$delivery"' EXIT; curl -qfsSL --proto '=https' --proto-redir '=https' https://raw.githubusercontent.com/serrja/setupSystem/main/install.sh -o "$delivery"; /bin/sh "$delivery" install )
```

APT installs the configurator and its runtime dependencies. Only the package transaction asks for sudo. After installation, open a terminal as the desktop user and run

```sh
/usr/bin/setup-system
```

If you previously used 0.4.x, check which launcher your shell finds.

```sh
command -v setup-system
```

Use `/usr/bin/setup-system` to open the Debian package when an older launcher takes precedence. The package leaves launchers in your home directory alone. To identify the old managed launcher, compare `sha256sum "$HOME/.local/bin/setup-system"` with the digest in `${XDG_STATE_HOME:-$HOME/.local/state}/setup-system/launcher.sha256`. If both regular files belong to you and the digest matches, you can archive that launcher under an unused name and start a fresh shell. Keep a custom or unrecognized launcher in place.

## Update or run

Use the same delivery script to update an installed package. It resolves the stable release once, verifies the Debian package, and asks APT to perform the update.

```sh
( set -eu; delivery=$(mktemp); trap 'rm -f -- "$delivery"' EXIT; curl -qfsSL --proto '=https' --proto-redir '=https' https://raw.githubusercontent.com/serrja/setupSystem/main/install.sh -o "$delivery"; /bin/sh "$delivery" update )
```

The GitHub command for a separate launch is

```sh
( set -eu; delivery=$(mktemp); trap 'rm -f -- "$delivery"' EXIT; curl -qfsSL --proto '=https' --proto-redir '=https' https://raw.githubusercontent.com/serrja/setupSystem/main/install.sh -o "$delivery"; /bin/sh "$delivery" run )
```

It opens the installed `/usr/bin/setup-system`. Once your shell resolves the installed launcher, the everyday command `setup-system` works offline from any directory. Debian removes the configurator with `sudo apt remove setup-system`. User selections, backups and workstation applications have their own lifecycle.

## Configure a workstation

Choose the settings you want and review the plan. Save writes a section's TOML configuration independently of Apply. Confirm the plan before entering the system password. A user-only action runs under your own account. You can switch tabs while the confirmed operation runs. Use a new preview after changing a selection or after an external edit.

See [configuration](docs/configuration.md), [recovery](docs/recovery.md), [development](docs/development.md) and [release verification](docs/release_guide.md).
