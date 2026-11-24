# setupSystem

Configure an existing Debian 13 (trixie) workstation on amd64 or arm64. The **Base system** tab handles Debian, Hyprland, regional settings and graphical login. The **Working environment** tab handles applications, the Catppuccin Mocha Green profile, Neovim, language tools and managed files.

## Install the configurator

Use a normal account on Debian 13 with configured package sources. Package installation needs authorization from sudo or the local root account. Remote delivery requires a POSIX shell, curl with CA certificates, jq, dpkg, APT, util-linux, coreutils and a GitHub CLI that supports signed build attestations with source and signer constraints. The delivery script checks the required flags. Downloading the package requires neither a checkout nor Make, uv or Python.

Install the current stable Debian package from GitHub. For a system with a root password and no sudo access, use the authenticated su option in the [administration guide](docs/administration.md).

```sh
( set -eu; delivery=$(mktemp); trap 'rm -f -- "$delivery"' EXIT; curl -qfsSL --proto '=https' --proto-redir '=https' https://raw.githubusercontent.com/serrja/setupSystem/main/install.sh -o "$delivery"; /bin/sh "$delivery" install )
```

APT installs the configurator and its runtime dependencies. Start it separately as the desktop user

```sh
/usr/bin/setup-system
```

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

## Use the configurator

Choose a section, review its plan and confirm Apply. F1 opens the installed guides. The terminal manual is available with `man setup-system`. [Configuration](docs/configuration.md) · [Administration](docs/administration.md) · [Recovery](docs/recovery.md) · [Testing](docs/testing.md)
