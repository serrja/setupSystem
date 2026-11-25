# Git

## ⚙️ Author profiles

Choose Configure author profile, enter an author and an existing directory, then review both paths. Git reads the identity fragment for repositories below that directory. A local repository setting can override it. The signing field accepts a full OpenPGP fingerprint. Check the effective identity inside a checkout with `git config --show-origin --get user.email`. [Git configuration](https://git-scm.com/docs/git-config).

## 🔐 Accounts

Sign in opens instructions for the GitHub CLI browser flow. Run Account status to verify the active account on the selected host. Configure an enterprise hostname in Settings before using it. Sign out removes credentials for one local account. Remote authorization remains under the account's control. [GitHub CLI login](https://cli.github.com/manual/gh_auth_login) · [GitHub CLI logout](https://cli.github.com/manual/gh_auth_logout).

## 🧹 Recovery

The managed-file journal preserves displaced config files and detects concurrent changes. Repository contents and existing SSH keys belong to the user.

## 🔐 SSH keys

Choose the OpenSSH public key and hosting account for Upload public SSH key. The result records its server ID and fingerprint. Verify SSH account access uses the chosen public key, strict known-host checking and noninteractive OpenSSH. Unlock the key in your agent first. Revoke owned SSH key checks the same ID and fingerprint before sending a deletion to the hosting service. [GitHub SSH connection test](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/testing-your-ssh-connection).


Create an SSH key opens Debian's root-owned GNOME OpenSSH passphrase dialog when a graphical session and `ssh-askpass-gnome` are available. OpenSSH writes an encrypted Ed25519 pair into a dedicated directory under `~/.ssh/setup-system` and returns its public fingerprint. Choose Upload public SSH key separately. In a terminal-only session, run `ssh-keygen -t ed25519`, choose an unused filename, enter the passphrase in OpenSSH and select the resulting public file for upload. A terminal-created identity remains user-owned. [OpenSSH key generation](https://man.openbsd.org/ssh-keygen) · [Debian GNOME OpenSSH prompt](https://packages.debian.org/trixie/ssh-askpass-gnome).

Full cleanup revokes reviewed plugin-created hosting keys first. The local-key step lists individual files and the public fingerprint before deleting an unchanged managed pair. Preserve any key that other hosts still use.


An interrupted local-key deletion retains a receipt for both files. Reopen Managed resources, inspect the interrupted operation and confirm the remaining cleanup. Until it finishes, the key name stays reserved. A replacement file or mismatched fingerprint requires review.


Preview names the private staging directory, `~/.ssh/setup-system/.generate-<key-name>`. If generation stops before publication, select the same key name to display the exact recovery path. The files there may be incomplete, and a rejected empty passphrase can leave an unencrypted private key. Keep the directory private. Check its owner and permissions and use `ssh-keygen -lf` on its public file to inspect the fingerprint. Move the entire staged directory to a private location you control before retrying. Review its contents yourself before any deletion. The manager leaves interrupted and unrecognized files in place.
