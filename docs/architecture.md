# Architecture

The Debian package installs the Python application under dist-packages and executable resources under `/usr/lib/setup-system`. The installed `setup-system` command selects the fixed `run` target there. Installation and update end at the package transaction.

## 🧭 Where to find the code

| Responsibility | Implementation |
| --- | --- |
| Form state, validation and immutable plan snapshots | `src/setup_system/models.py`, `app.py`, `ui/` |
| Section settings, revisions and migration | `src/setup_system/settings.py` |
| Completion and catalog selections | `src/setup_system/suggestions.py`, `config/` |
| Running an operation and handling its single password prompt | `src/setup_system/runner.py`, `libexec/play.sh` |
| Command sequence and per-task privileges | `mk/`, `ansible/` |
| Compare-and-swap publication of user files | `src/setup_system/file_tasks.py`, file publication and receipt modules |
| Delivery, native package metadata and source identity | `install.sh`, `debian/`, `mk/package.mk` |

After confirmation, Textual writes the selections and observations to a private JSON request. Make receives a fixed target and passes the request path to a packaged play. A user string cannot select a Makefile, playbook or command. Ansible validates the typed selection and compares the current native plan before applying it. The package owns the plays, templates and executables used by system tasks.

The Textual app and the Ansible controller run as the desktop user. System tasks use native Ansible become. The app sends a single password to its dedicated PTY after the expected prompt and disabled terminal echo. Terminal input and output are excluded from persistent logs. Distribution packages provide Ansible and its Python dependencies.

The section forms and worker have separate lifetimes. Editing a form changes the next preview. Package transactions and mutating tasks use an exclusive lock. File publication checks the previously observed content and owner, records partial progress, and verifies written files before marking them complete. The same receipt supports restore after an interrupted run.

## Async execution

A command captures one target and a validated request. Noninteractive commands use asyncio subprocesses. The privileged Make targets require the root-owned Debian installation. The password path responds to the native no-echo Ansible prompt in a dedicated PTY. The runner keeps ownership of the process and lock until completion or a safe cancellation boundary. The UI displays structured progress and operation outcomes. Stable action kinds identify executable changes. The UI can change their display labels independently.

The settings worker holds the complete ownership, revision, flock, fsync and rename transaction. Quit waits for the writer. Path completion debounces edits and limits the scan and cache.

## Native tools and retained adapters

| Responsibility | Native tool and project-specific check |
| --- | --- |
| Packages and applications | APT resolves dependencies. The pre-dpkg hook checks the confirmed closure and the Debian release. npm, pipx and Git use reviewed sources and per-run artifact identities. |
| File publication and recovery | Ansible renders templates. The publication adapter retains displaced inodes and verifies hashes, mode, owner and conflicts during Restore. |
| System login and boot | greetd, PAM, GRUB, cryptsetup and initramfs-tools own their respective operations. The adapters check account identity, mount layout, recovery menu and backups. |
| Projects and displays | Git, Copier and Hyprland perform changes. The adapters constrain paths, protect local edits and restore the previous display layout after a timed preview. |
| Administrative onboarding | Native su authenticates root. The Debian user module appends the confirmed account to the local sudo group. Account and sudo policy checks run again before the group change. |
| Editor and shell | Neovim manages plugins, parsers and language tools. The editor proof checks headless operation before cleanup. Shell, Git and Codex adapters preserve settings outside the managed scope. |
| Packaging | dpkg-deb builds the archive. The packaging recipe reads tracked blobs from the exact Git commit and records the source SHA. The project retains this path for reproducibility, lifecycle locks and the current provenance contract. debhelper, dh-python and pybuild are alternatives for the filesystem layout; a switch requires reproducing those properties on the builder. |
