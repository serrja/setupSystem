# Project workspace

The [project catalog](../manifests/projects.toml) is the single source for `projectsRoot`, owner groups, checkout destinations and known legacy paths. Its default is `$HOME/Projects`. `Projects`, `serrja` and `pokerlost` are ordinary directories; every checkout keeps its own `.git`. A declaration never clones a repository or creates a remote.

## 🧭 Layout and commands

`serrja` holds personal tooling and the two generic template sources. `pokerlost` holds the separate Pokerlost repositories. The logical/local `pythonTemplate` destination retains the `serrja/projectTemplates` remote and its history; renaming that remote is a separate decision. `fastApiTemplate`, `pokerService`, `pokerClient` and `cryptoService` now have separate local Git checkouts at their recorded legacy paths, without GitHub remotes or relocation to their target owner directories. `virtualMoney` is `sourcePending`: the archive does not establish a complete local Git history.

From this checkout, the source CLI can be inspected without installing it or applying system configuration:

```bash
UV_CACHE_DIR="$HOME/.cache/setupSystemNextStage" uv run --no-sync setup-system projects list
UV_CACHE_DIR="$HOME/.cache/setupSystemNextStage" uv run --no-sync setup-system projects planMigration
UV_CACHE_DIR="$HOME/.cache/setupSystemNextStage" uv run --no-sync setup-system templates list
```

After installation, use `setup-system` directly. `projects list` and `projects path --name serrja/jobdesk` resolve only exact Git roots from the catalog. `templates path --name pythonTemplate` resolves the same source for an explicit Copier invocation; it does not render or install anything. `projects open --name serrja/jobdesk` launches Neovim there; `--tmux` uses that root for a new session. `agents launch serrja/jobdesk --mode review` resolves catalog entries at launch. Existing user-owned `scopes.toml` entries remain available for unrelated names and are not rewritten by the picker. Old short names such as `jobdesk` are rejected with a migration hint, so a stale legacy scope cannot launch the wrong checkout after a move.

On a **supported Debian** host, `setup-system projects prepare --yes` explicitly creates only `$HOME/Projects` and its owner directories, after checking for symlinks, non-directories and Git superprojects. It does not create checkout directories, clone repositories or run during shell/editor startup. On the current Ubuntu host the command returns `UNSUPPORTED` before a write.

## 📦 Relocation procedure

`projects planMigration` is read-only and works on Ubuntu. Its `old → target` lines are the current mapping. `backupRequired` is a candidate for a later, separately scoped move; `review` and `deferredActive` need their stated issue resolved first. An initialized local repository without a first commit is reported as `UNBORN` and requires review; no commit is made by planning. The plan reports HEAD or `UNBORN`, branch/upstream, outgoing count, `.git` kind, worktree/submodule counts, dirty counts and local environment presence without printing file contents. The catalog marks `mloso` origins for `jobdesk` and `accountsService` for owner review rather than silently treating the proposed owner group as verified.

For each approved source/destination pair, the relocation writer must:

1. Stop writers using **that checkout** and record its actual Git root, remote, HEAD, index digest, staged/unstaged binary patch digests, untracked and significant ignored files, modes, symlink targets, submodules and worktrees. Make a private full-tree backup outside distributed repositories, with a hash/mode manifest and recovery instructions. The regular `setup-system backup` covers configuration files; it is not this full-checkout backup.
2. Reject a nonempty destination, an owner directory with `.git`, a symlink traversal and an origin mismatch. On one filesystem, move an ordinary checkout as a single tree with `.git`. For a linked worktree use Git's supported `worktree move`/`repair` procedure. Across filesystems, stage a copy, verify it, then switch; never merge trees or remove the source before verification.
3. Before semantic edits, compare HEAD, index, staged/unstaged patch, significant untracked/ignored content, modes, symlinks and origin at the destination. Auth/accounts dirty foundation patches must be byte-identical at this boundary. Keep the recovery copy private and clearly inactive.
4. Review dependent paths: local agent scopes/grants, editable installs, template sources/provenance, Git `includeIf`, picker/editor/tmux launchers and live documentation links. Do not rewrite history, client session databases or old backup manifests. Recreate project-owned `.venv` and native build caches from locks/build configuration at the new path; their absolute paths are not portable.
5. Move `setupSystem` last, from a process whose cwd and executable are outside that tree. Confirm the installed wheel-backed launcher still runs before removing its old path. Run `projects list`, `projects planMigration` and each project's narrow checks after retargeting. Do not retain a permanent compatibility symlink.

The current CLI **plans** moves and prepares owner directories on Debian; it does not execute relocation. No repository is moved by `apply`, `projects list`, Neovim or shell startup. Git documents [linked worktree move/repair](https://git-scm.com/docs/git-worktree); Python documents why [virtual environments are not portable](https://docs.python.org/3/library/venv.html).

## 🔒 Scope and recovery

Each source edit uses its exact repo root. Relocation needs a separate permission scope for the approved pair and private backup/state path. A catalog entry, directory name or `AGENTS.md` is not a filesystem grant. On failure, stop before deleting the source; inspect the private manifest and either finish verification or restore that checkout as a whole. Do not use `reset --hard`, blind stash, `git clean` or force push.
