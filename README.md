# setupSystem

Configure an existing Debian 13 workstation with a Textual interface. **Base system** covers Hyprland, graphical login and regional settings. **Working environment** covers the Catppuccin Mocha desktop, Neovim, applications and projects.

## 📦 Install

Paste this command into a terminal as your regular user on Debian 13 (trixie), amd64 or arm64. You need curl, the GitHub CLI with attestation support, and access to APT. [Full prerequisites and the su authentication option](docs/debian.md)

```sh
sh -c 'loader=$(curl -qfsSL --proto =https --proto-redir =https "$1") && exec /bin/sh -c "$loader" install.sh install' sh \
  https://raw.githubusercontent.com/serrja/setupSystem/main/install.sh
```

The loader verifies the stable Debian package and its build attestation before asking for permission to install it. Installation ends when APT finishes.

## 🔄 Update

Run the installed package’s update recipe as your regular user. It verifies the new release and finishes after APT completes.

```sh
make -C /usr/lib/setup-system update
```

## 🚀 Run

Open the installed app from any directory.

```sh
setup-system
```

Choose a section, inspect the machine and review the plan before selecting **Apply**. Press **F1** for the offline guides.

## 📚 Guides

- [Update or remove the package](docs/debian.md)
- [Configure the workstation](docs/textual_workflow.md)
- [Restore managed files](docs/recovery.md)
- [Build and test from source](docs/development.md)
