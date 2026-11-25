# Graphical login

Open **Hyprland** to prepare the ordinary user's session launcher. Install the Debian dependencies on the **Greeter** page. The **Activate graphical login** action reviews the selected login and proposed systemd changes. Greetd runs the GTK greeter under the dedicated `_greetd` account. PAM authenticates the selected user and a system-owned launcher checks the UID, HOME and XDG directories.

## 🔐 Activate the login manager

Review the files, existing display manager and current default target. The executor revalidates the account, backs up managed files, enables `greetd.service` and selects `graphical.target` for a future boot. A different display manager or a conflicting local file blocks activation. Session registration requires access to the selected user's HOME. The current desktop stays open.

Hyprlock authenticates the owner of an existing session. Its password field and the GTK greeter share colors, fonts and geometry. Graphical disk unlock uses Plymouth during initramfs. [Desktop appearance](theme.md) · [Graphical disk unlock](graphical_unlock.md)

## ⚙️ Recovery and acceptance

Use a local text console if the graphical login fails. Review the active display-manager alias, default systemd target, journal and root-owned recovery records under `/var/lib/setup-system`. Retain checked backups when local edits conflict with restoration. Verify a real login, expired credentials, locking, fallback TTY and a second ordinary account only on the authorized disposable target. [Acceptance and evidence](testing.md)

Open the extension’s **Help** tab for offline instructions. [Greeter Help](../plugins/desktop/greeter/src/setup_plugin_greeter/help.md) · [Hyprlock Help](../plugins/desktop/hyprlock/src/setup_plugin_hyprlock/help.md)
