# setupSystem

setupSystem manages programs and workstation settings on an existing Debian 13 (trixie) desktop, amd64 or arm64. Open a program's page to configure profiles, check its current state, manage accounts or run an individual action. A workstation profile combines actions from several extensions.

## 📦 Install and update

From a normal Debian user account with curl and CA certificates, run the command below. The loader downloads its verification tools to a temporary directory, checks their pinned SHA-256 digests, then verifies the stable Debian package and its GitHub build attestation. APT requests administrator authorization to install the configurator. [Prerequisites and delivery options](docs/debian.md)

```sh
sh -c 'loader=$(curl -qfsSL --proto =https --proto-redir =https "$1") && exec /bin/sh -c "$loader" install.sh install' sh \
  https://raw.githubusercontent.com/serrja/setupSystem/main/install.sh
```

Start the configurator as your normal user in a terminal or a text console (TTY).

```sh
setup-system
```

You can also open **setupSystem** from the desktop applications menu. For an installed package, run `make -C /usr/lib/setup-system update`. The source checkout supports `make run`.

## ⚙️ Set up the workstation

1. From the text console, use **Administration** if you need to prepare sudo access. Review the **Base workstation** profile or select individual actions in **Regional**, **Hyprland** and the other desktop cards. Sign into the configured desktop when it is ready. [Desktop login](docs/desktop_login.md)
2. Open setupSystem from the desktop. Install **GitHub CLI** or other clients when you need them, then choose their account actions or sign in directly in the official client. [Application actions](docs/applications.md)
3. Install **Snap** and start its service if needed. Choose **List installed snaps** to see the installed revisions. Install the official **Telegram Desktop** snap from its own card and sign in inside Telegram.

## 🧩 Open an extension

Search the catalog or select a category. Open a card and choose **Activate / update extension** to prepare its isolated backend with uv. If uv is missing, this first activation downloads and verifies the pinned Astral release for the machine. Read the source, author, release digest and requested permissions before activation. Program installation has its own **Install** action. **Add from link** accepts a verified HTTPS release manifest or a compatible binary wheel. The administrator's source policy and the approved digest must cover the complete executable dependency chain. [Extension trust and delivery](docs/plugin_trust.md)

Use **Settings / Profiles** to edit and save preferences. Under **Actions**, select the command, review its parameters and choose **Preview**. Apply the snapshot after checking the listed resources, account, permissions and recovery options. Saved profiles and operation history remain available when you return to the card. **Help** opens the packaged instructions. [Using the configurator](docs/textual_workflow.md)

## 🧹 Uninstall and recover

**Uninstall** previews program removal. **Disable extension** keeps its environment for later use. **Remove extension** verifies and removes its code. **Full cleanup** previews owned resources, supported remote revocations and the extension environment. Review preserved resources and resolve ownership conflicts before continuing. Repositories, vault notes, attachments and Docker volumes remain user data. Verified file operations have a separate rollback receipt. [Recovery and ownership](docs/recovery.md)

## ⚙️ Engineering

Read the [SDK guide](docs/plugin_authoring.md), [settings reference](docs/settings_map.md), [native development gate](docs/development.md) and [release guide](docs/release_guide.md).
