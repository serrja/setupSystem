# Git

## ⚙️ Author profiles

Choose Configure author profile, enter an author and an existing directory, then review both paths. Git reads the identity fragment for repositories below that directory. A local repository setting can override it. The signing field accepts a full OpenPGP fingerprint. Check the effective identity inside a checkout with `git config --show-origin --get user.email`. [Git configuration](https://git-scm.com/docs/git-config).

## 🔐 Accounts

Select Sign in, enter the expected account name and confirm. GitHub CLI opens its browser flow from a separate terminal and skips SSH key upload. After that terminal closes, the action checks the expected active account and its keyring token source through GitHub CLI. Debian stable’s older client can confirm the account through the user API. Run `gh auth status` to check its credential location when the action asks. Unlock the default Secret Service collection first. If the collection fails during sign-in, GitHub CLI can save its token in hosts.yml. Check the location with `gh auth status` and review the credential in GitHub CLI. If a local Wayland terminal is unavailable, follow the command on the plugin page. Configure an enterprise hostname in Settings before using it. Status and Sign out select an existing local login and host. The account selector reads public names from the private GitHub CLI hosts file. Newer clients also report their inventory as JSON. Sign out checks the selected identity online, invokes the native account-specific logout, and verifies the local account is absent and the host reachable. If the client lacks a user selector, install a version supporting `gh auth logout --hostname HOST --user ACCOUNT`. Remote authorization remains under the account's control. See the [Debian 13 client’s account handling](https://github.com/cli/cli/blob/v2.46.0/internal/config/config.go). [GitHub CLI login](https://cli.github.com/manual/gh_auth_login) · [GitHub CLI logout](https://cli.github.com/manual/gh_auth_logout).

## 🧹 Recovery

The managed-file journal preserves displaced config files and detects concurrent changes. Repository contents and existing SSH keys belong to the user.

## 🔐 SSH keys

Choose the OpenSSH public key and hosting account for Upload public SSH key. The result records its server ID and fingerprint. Verify SSH account access uses the chosen public key, strict known-host checking and noninteractive OpenSSH. Unlock the key in your agent first. Revoke owned SSH key checks the same ID and fingerprint before sending a deletion to the hosting service. [GitHub SSH connection test](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/testing-your-ssh-connection).


Create an SSH key opens Debian's root-owned GNOME OpenSSH passphrase dialog when a graphical session and `ssh-askpass-gnome` are available. OpenSSH writes an encrypted Ed25519 pair into a dedicated directory under `~/.ssh/setup-system` and returns its public fingerprint. Choose Upload public SSH key separately. In a terminal-only session, run `ssh-keygen -t ed25519`, choose an unused filename, enter the passphrase in OpenSSH and select the resulting public file for upload. A terminal-created identity remains user-owned. [OpenSSH key generation](https://man.openbsd.org/ssh-keygen) · [Debian GNOME OpenSSH prompt](https://packages.debian.org/trixie/ssh-askpass-gnome).

Full cleanup revokes reviewed plugin-created hosting keys first. The local-key step lists individual files and the public fingerprint before deleting an unchanged managed pair. Preserve any key that other hosts still use.


An interrupted local-key deletion retains a receipt for both files. Reopen Managed resources, inspect the interrupted operation and confirm the remaining cleanup. Until it finishes, the key name stays reserved. A replacement file or mismatched fingerprint requires review.


Preview names the private staging directory, `~/.ssh/setup-system/.generate-<key-name>`. If generation stops before publication, select the same key name to display the exact recovery path. The files there may be incomplete, and a rejected empty passphrase can leave an unencrypted private key. Keep the directory private. Check its owner and permissions and use `ssh-keygen -lf` on its public file to inspect the fingerprint. Move the entire staged directory to a private location you control before retrying. Review its contents yourself before any deletion. The manager leaves interrupted and unrecognized files in place.
