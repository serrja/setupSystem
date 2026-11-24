# Administrative access

In **Base system**, select the existing local account that needs administrative access. Run the configurator as your ordinary user. Preview shows the UID, primary GID, HOME, shell and current sudo membership. Granting access allows that login to run commands as root with its own password. Confirm the account and consequence before entering the **root password** for the packaged Ansible `su` operation.

## 📦 First installation without sudo

If sudo is unavailable before installation, run `sh install.sh install --auth su` as the ordinary user from the reviewed release bootstrap. Add `--release vX.Y.Z` to select a published stable version. The bootstrap verifies the GitHub release, checksum and attestation before invoking native `su` for the package transaction. APT checks its installed state and the transaction rechecks the selected package bytes. Start the application separately with `setup-system`. If the installer reports that an older launcher takes precedence in PATH, use the full path it displays. Updates support the same explicit authentication choice. Default package delivery uses sudo.

## 🔐 Grant and verify access

The account must have a unique local passwd entry, an ordinary UID, a local primary group, an owned HOME and an enabled interactive shell. The confirmed snapshot binds these facts and the supplementary groups to the current system boot. The packaged helper checks them again, validates the effective files-based sudoers configuration with `visudo` and `cvtsudoers`, then uses Ansible's existing-user backend with `append` for the single local `sudo` group. The backend may invoke only the native group append for this account. It verifies the resulting membership and lists effective root access through sudo. The native account operation uses a login name. Another administrator must serialize account replacement around this brief check and update. A post-write identity or policy conflict requires administrator review.

The append adapter supports the reviewed Ansible 2.19 and 2.20 backends. The automatic flow supports the stock Debian root and password-authenticated `%sudo` grants and standard global Defaults. Scoped or additional grants, passwordless policy, alternate policy plugins or network account sources require an authorized administrator to evaluate local policy. An already enrolled account produces an empty membership change. A fresh login loads the new supplementary group. Later system actions use the ordinary sudo prompt.

Root account lockout, PAM refusal and unavailable `su` require a separate authorized administrator. This operation preserves passwords, PAM, SSH, all other group memberships and active sessions. The LUKS password belongs to the boot prompt.

See the [Debian sudo policy checker](https://manpages.debian.org/trixie/sudo/visudo.8.en.html), [policy converter](https://manpages.debian.org/trixie/sudo/cvtsudoers.1.en.html) and [Ansible su method](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/su_become.html).
