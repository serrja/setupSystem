# Administrative access

In **Base system**, select the existing local account that needs administrative access. Run the configurator as your ordinary user. Preview shows the UID, primary GID, HOME, shell and current sudo membership. Granting access allows that login to run commands as root with its own password. Confirm the account and consequence before entering the **root password** for the packaged Ansible `su` operation.

## 📦 First installation without sudo

If sudo is unavailable before installation, run `sh install.sh install --auth su` as the ordinary user from the reviewed release bootstrap. Add `--release vX.Y.Z` to select a published stable version. The bootstrap verifies the GitHub release, checksum and attestation before invoking native `su` for the package transaction. APT checks its installed state and the transaction rechecks the selected package bytes. Start the application separately with `setup-system`. If the installer reports that an older launcher takes precedence in PATH, use the full path it displays. Updates support the same explicit authentication choice. Default package delivery uses sudo.

## 🔐 Grant and verify access

The account must have a unique local passwd entry, an ordinary UID, a local primary group, an owned HOME and an enabled interactive shell. The confirmed plan binds its identity, supplementary groups and the local sudo group to the current boot. Immediately before the append, the installed helper compares these facts and verifies the local sudo policy with `visudo` and `cvtsudoers`.

Ansible's user module can create a missing login with `state=present`. This operation requires an existing login, so the packaged helper runs `usermod --append --groups sudo -- LOGIN` through `ansible.builtin.command` with an argument list. The UI requests the root credential once. Ansible first uses it for a read-only `su` probe, then for the single packaged grant. A failed probe blocks the grant. A missing or invalid reply after the grant attempt records an unknown outcome. Inspect the account and current sudo policy before retrying. Its local Ansible command uses a private root-owned temporary directory. The helper reads back identity and supplementary groups and checks effective root access with `sudo -l`. External administrators must serialize account replacement while this name-based operation runs. A changed identity, lost group or unverified policy after the append requires inspection. [Ansible user](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/user_module.html) · [Ansible command](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/command_module.html) · [Debian usermod](https://manpages.debian.org/trixie/passwd/usermod.8.en.html)

The automatic flow supports the stock Debian root and password-authenticated `%sudo` grants and standard global Defaults. Scoped or additional grants, passwordless policy, alternate policy plugins or network account sources require an authorized administrator to evaluate local policy. An already enrolled account produces an empty membership change. A fresh login loads the new supplementary group. Later system actions use the ordinary sudo prompt.

Root account lockout, PAM refusal and unavailable `su` require a separate authorized administrator. This operation preserves passwords, PAM, SSH, all other group memberships and active sessions. The LUKS password belongs to the boot prompt.

See the [Debian sudo policy checker](https://manpages.debian.org/trixie/sudo/visudo.8.en.html), [policy converter](https://manpages.debian.org/trixie/sudo/cvtsudoers.1.en.html) and [Ansible su method](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/su_become.html).
