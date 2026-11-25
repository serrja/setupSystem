# Git

## ⚙️ Author profiles

Choose Configure author profile, enter an author and an existing directory, then review both paths. Git reads the identity fragment for repositories below that directory. A local repository setting can override it. The signing field accepts a full OpenPGP fingerprint. Check the effective identity inside a checkout with `git config --show-origin --get user.email`. [Git configuration](https://git-scm.com/docs/git-config).

## 🔐 Accounts

Sign in opens instructions for the GitHub CLI browser flow. Run Account status to verify the active account on the selected host. Configure an enterprise hostname in Settings before using it. Sign out removes credentials for one local account. Remote authorization remains under the account's control. [GitHub CLI login](https://cli.github.com/manual/gh_auth_login) · [GitHub CLI logout](https://cli.github.com/manual/gh_auth_logout).

## 🧹 Recovery

The managed-file journal preserves displaced config files and detects concurrent changes. Repository contents and existing SSH keys belong to the user.

## 🔐 SSH keys

Choose the OpenSSH public key and hosting account for Upload public SSH key. The result records its server ID and fingerprint. Verify SSH account access uses the chosen public key, strict known-host checking and noninteractive OpenSSH. Unlock the key in your agent first. Revoke owned SSH key checks the same ID and fingerprint before sending a deletion to the hosting service. [GitHub SSH connection test](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/testing-your-ssh-connection).
