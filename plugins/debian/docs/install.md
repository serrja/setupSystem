# Debian workstation target

After installing the Debian package, choose **setupSystem** in the desktop applications menu or enter this command in any terminal or text console (TTY) as your normal user.

```sh
setup-system
```

Search for a plugin, activate its backend, then preview the program action you want. When uv is missing, the first explicit activation fetches the pinned Astral binary and checks its archive digest. APT owns the configurator, bundled wheels, protected resources and offline documentation. Program settings and file recovery records are stored under the invoking user's XDG directories. Open the guides with F1 or `man setup-system`. [Using the configurator](../../../docs/textual_workflow.md) · [Settings](../../../docs/configuration.md) · [Recovery](../../../docs/recovery.md)

## 📦 Install on Debian

Use Debian 13 (trixie) on amd64 or arm64 with configured APT sources, a normal user account, curl and CA certificates. The loader uses Debian's standard shell, dpkg, APT, util-linux and coreutils. It stages its verifier and JSON reader in a private executable temporary directory and removes them at exit. A GitHub account, checkout, preinstalled GitHub CLI, jq and uv are unnecessary.

If curl is missing, install the download prerequisite with your existing administrative access.

```sh
sudo apt update
sudo apt install ca-certificates curl
```

Download the delivery script as your normal user. With no arguments, the script runs its `install` action. The script is the bootstrap trust anchor. For an independently reviewed revision, substitute its full commit SHA for `main` in the download URL. The loader resolves the latest stable release once and checks both canonical and companion tag identities. Before APT requests authorization, it checks the Debian package digest, the signed build's main commit, workflow and runner, and the published plugin catalog. Installation ends after APT installs the configurator and its runtime dependencies.

```sh
curl -fsSL https://raw.githubusercontent.com/serrja/setupSystem/main/plugins/debian/packaging/install.sh -o install.sh && sh install.sh
```

After downloading the script, you can also run `sh install.sh install` explicitly. If only the local root account can authorize installation, run `sh install.sh install --auth su` with the downloaded file. Native `su` will ask for the root password. For the usual sudo path, use the installing user's sudo password. [Administrative access](../../administration/docs/administration.md)

The loader pins distribution-specific SHA-256 digests for the official Linux amd64 and arm64 builds of GitHub CLI and jq. It authenticates each download before unpacking or executing it as the normal user. The Sigstore verifier runs with a private HOME and a minimal environment. To refresh these tools, maintainers review the upstream release, compare the release asset digests with the published checksum files, update the pins in `install.sh` and verify a published package on the new verifier before release. [GitHub CLI 2.105.0 checksums](https://github.com/cli/cli/releases/download/v2.105.0/gh_2.105.0_checksums.txt) · [jq 1.8.2 checksums](https://github.com/jqlang/jq/releases/download/jq-1.8.2/sha256sum.txt)

## 🔄 Update the configurator

Run the same delivery script with `update` to verify and install the latest stable package. Download a fresh reviewed loader when replacing its pinned verification tools. The update ends at the APT transaction. Open the app yourself when you are ready.

```sh
( set -eu
  delivery=$(mktemp)
  trap 'rm -f -- "$delivery"' EXIT
  curl -qfsSL --proto '=https' --proto-redir '=https' \
    https://raw.githubusercontent.com/serrja/setupSystem/main/plugins/debian/packaging/install.sh -o "$delivery"
  /bin/sh "$delivery" update
)
```

## 🗑️ Remove the configurator

```sh
sudo apt remove setup-system
```

APT removes the configurator. The workstation applications, user settings and backups stay on disk. To restore managed files, preview **Restore** in the app before removing it. [Recovery](../../../docs/recovery.md)

## 🖥️ Inspect the target

System changes require a matching distribution, release, codename and architecture. The configurator works with the existing filesystems, boot loader and account. [Linux execution boundaries](linux.md)

## System packages

Open the plugin that owns the program. For example, **Hyprland** manages the compositor session, **Waybar** manages the panel and **Greeter** manages graphical login. Preview the selected action to review its package changes before applying them. Debian package operations use signed APT metadata and recheck the approved closure and installed state. A failed transaction can leave some packages installed. Check its result and Debian's package logs before retrying. [Desktop login](../../greeter/docs/desktop_login.md) · [Recovery](../../../docs/recovery.md)

## Regional settings

Timezone, locale and keyboard each have their own field. An empty field preserves the corresponding system value. **Inspect** refreshes suggestions from installed zoneinfo, Debian's UTF-8 locale list and XKB rules. The plan compares each selection with the active system and includes any required Debian packages.

Locale generation retains existing selections. The keyboard operation preserves unrelated entries in `/etc/default/keyboard` and uses `setupcon --keyboard-only --save-only` to prepare the console keymap. An updated console layout takes effect in subsequent sessions. Each operation verifies its result and stores recovery evidence under `/var/lib/setup-system/regional/`.
