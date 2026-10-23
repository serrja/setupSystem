# Git author profiles

An author identity (`user.name`, `user.email`, optional `user.signingKey`), a GitHub
authentication account and a repository owner are separate concepts. An organization
is not an author identity. These commands configure authors; they do not create accounts,
change SSH keys, credential helpers, remotes, signing policy or historical commits.

## Inspect

Use the exact root of an existing checkout. Replace the example paths with reviewed
local paths; none of the examples provides a real name, email or key.

```bash
setup-system git profile list
setup-system git profile show --repo "$HOME/Projects/owner/project"
```

`show` queries only author/signing fields, with Git scope and origin. The last value
for each key is effective unless environment variables override it; their presence is
reported without their contents. Signing key values, including multiline values, are hidden. Control characters in
other displayed values are escaped. Author inspection is
not an SSH authentication check. Do not publish personal command output.

From an already prepared checkout, prefix these commands with `uv run --no-sync`
to use its current environment without installing or updating dependencies. A globally
installed older CLI stays unchanged until you explicitly update that installation.

## Directory defaults

First confirm the identity already configured in the source checkout. If only one
identity exists, keep one profile; directory ownership is not evidence for another one.
`configure` reads the existing identity without guessing it from account or owner names.

```bash
setup-system git profile configure default \
  --source-repo "$HOME/Projects/owner/project" \
  --directory "$HOME/Projects/owner"
# Apply the reviewed plan only after granting these exact user-config paths:
setup-system git profile configure default \
  --source-repo "$HOME/Projects/owner/project" \
  --directory "$HOME/Projects/owner" --yes
```

Repeat `--directory` to select another existing directory for the same identity.
The profile lives at `${XDG_CONFIG_HOME:-$HOME/.config}/git/identities/default.gitconfig`.
Git's native `includeIf "gitdir:/exact/directory/"` in `~/.gitconfig` selects it for
checkouts below that directory. An explicit `GIT_CONFIG_GLOBAL` selects that file
instead. No branch conditions are used. Existing global defaults and unrelated
configuration, including setupSystem's style include, remain intact.

The profile sets `user.useConfigOnly=true` together with explicit name/email; it
does not set this flag globally or disable existing global defaults elsewhere.
Existing profiles with different values and conflicting directory includes require
review. The command does not replace them. A repeated identical application is a no-op.

## One repository

```bash
setup-system git profile use default --repo "$HOME/Projects/owner/project"
setup-system git profile use default --repo "$HOME/Projects/owner/project" --yes
setup-system git profile show --repo "$HOME/Projects/owner/project"
```

`use` writes explicit author fields to that checkout's `.git/config`, overriding its
directory defaults. This is a deliberate local copy: changing a profile later does not
silently change an existing local override; run `use` again when desired. An absent
profile signing key preserves the checkout's existing key and signing policy. Neither
`main`, `dev` nor `test` changes the selected author. Bare repositories, linked worktrees,
per-worktree configuration and symlinked config paths require separate review.

If there are several confirmed GitHub authentication accounts, manage their existing
SSH aliases/credential entries separately. Do not change the global `github.com` SSH
host or rewrite all remotes to switch an author profile.

## Native Git equivalent

The CLI wraps native Git with validation, a read-only plan and backups. These targeted
reads show author values and their sources without querying credentials or keys:

```bash
repo="$HOME/Projects/owner/project"
git -C "$repo" config --includes --show-origin --show-scope --get user.name
git -C "$repo" config --includes --show-origin --show-scope --get user.email
```

After a separate backup, the equivalent local author override is:

```bash
profile="${XDG_CONFIG_HOME:-$HOME/.config}/git/identities/default.gitconfig"
git -C "$repo" config --local --replace-all user.name "$(git config --file "$profile" --no-includes --get user.name)"
git -C "$repo" config --local --replace-all user.email "$(git config --file "$profile" --no-includes --get user.email)"
git -C "$repo" config --local --replace-all user.useConfigOnly true
```

These three writes preserve existing signing configuration. When the reviewed profile
also contains a signing key, `profile use` copies it without printing its value.
Prefer the CLI for writes when its backup and refusal checks are needed.

## Backup and restore

Every changing `--yes` operation saves existing files before Git writes them. A private
receipt under `${XDG_STATE_HOME:-$HOME/.local/state}/setup-system/backups/` records
before/after hashes and created paths. Profile files are created with mode 0600;
backups remain private. Their contents can include personal configuration: do not
commit or upload them.

```bash
setup-system git profile restore BACKUP_ID
setup-system git profile restore BACKUP_ID --yes
```

Restore checks the whole operation before changing anything, refuses later edits,
restores prior files and removes only files created by that operation. Restore newer
operations before older ones. It preserves a fresh backup of current files first;
this is file recovery, not Git-history or authentication rollback. A failed operation
reports its private backup for review. No command fetches, commits, pushes or contacts
GitHub. Ordinary verification remains lint-only; an authentication check needs its
own explicit authorization.
