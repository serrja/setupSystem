# Architecture

The Debian package installs the Python application under dist-packages and executable resources under `/usr/lib/setup-system`. The installed `setup-system` command selects the fixed `run` target there. Installation and update end at the package transaction.

| Responsibility | Implementation |
| --- | --- |
| Form state, validation and immutable plan snapshots | `src/setup_system/models.py`, `app.py`, `ui/` |
| Section settings, revisions and migration | `src/setup_system/settings.py` |
| Completion and catalog selections | `src/setup_system/suggestions.py`, `config/` |
| Running an operation and handling its single password prompt | `src/setup_system/runner.py`, `libexec/play` |
| Command sequence and per-task privileges | `mk/`, `ansible/` |
| Compare-and-swap publication of user files | `src/setup_system/file_tasks.py`, file publication and receipt modules |
| Delivery, native package metadata and source identity | `install.sh`, `debian/`, `mk/package.mk` |

The app writes a private JSON request containing the selections and confirmed observations. Make receives a fixed target and passes the request path to a packaged play. A user string cannot select a Makefile, playbook or command. Ansible validates the typed selection and compares the current native plan before applying it. The package owns the plays, templates and executables used by system tasks.

The Textual app and the Ansible controller run as the desktop user. System tasks use native Ansible become. The app sends a single password to its dedicated PTY after the expected prompt and disabled terminal echo. Terminal input and output are excluded from persistent logs. Distribution packages provide Ansible and its Python dependencies.

The section forms and worker have separate lifetimes. Editing a form changes the next preview. Package transactions and mutating tasks use an exclusive lock. File publication checks the previously observed content and owner, records partial progress, and verifies written files before marking them complete. The same receipt supports restore after an interrupted run.
