# Architecture

`setup-system` and `python -m setup_system` open the same Textual application.
The release bootstrap delivers that application to the selected user account.

## Responsibilities

| Boundary | Owns |
| --- | --- |
| `domain` | Settings and catalog contracts, observed facts, plan stages, system commands and recovery records |
| `application` | Planning, confirmation, ordered execution, cancellation, workspace tools and file recovery |
| `infrastructure` | Release resource reads, host inspection, TOML storage, file publication, Ansible Runner and native system adapters |
| `adapters/debian` | Signed APT acquisition, dependency resolution, offline installation, service deferral and package checkpoints |
| `tui` | Persistent tabs, subject-specific forms, previews and progress |
| `bootstrap` | Verification of the published source and preparation of the user and system runtimes |
| `resources`, `assets`, `manifests` | Release-owned catalogs, locks, roles, templates and palette |

`composition.py` binds the installed resources and native adapters to the application.
The App owns the running worker across tab changes. A confirmed plan captures an
independent copy of the selections, typed stages and observed revisions. Later edits
belong to the next plan. The controller admits one writer and checks cancellation
between completed stages.

The catalog repository reads resources from the selected checkout or installed wheel.
It validates platform aliases, source pins, component IDs and built-in handlers. The
planner uses the same component catalog and platform policy for both sections.
Domain models validate their inputs in memory. The screen and the running service
share the settings stores bound at startup. Apply checks their revisions again
before executing a confirmed plan. Saving preserves TOML comments. [Configuration](configuration.md)

## Privilege and source trust

The UI validates a typed system command and passes bounded JSON through native sudo.
The fixed system interpreter starts with `-I -B -S`. The stdlib bootstrap checks the
published tag, commit, platform, caller, source archive and native receipt.

The verified release carries the five distributions in the Pydantic dependency
closure, including a native extension for each supported Debian 13 architecture.
The bootstrap selects the wheel for the system ABI and compares its size and SHA-256
with the verified release's `uv.lock`. It extracts ordinary files into a private
directory under root-controlled state for the lifetime of one command. Wheel
installation, build hooks and site startup are disabled. The service loads the
same command schema as the UI and rechecks caller, XDG paths, package closure and
boot facts before making changes.

The APT adapter stages signed packages and records its installation checkpoints.
User configuration uses the finite Ansible role from the verified release.
File recovery compares the current contents, ownership and mode with the receipt
before it attempts restoration. [Recovery](recovery.md)

## Published contracts

The distribution, the `setup_system` package and the Textual entrypoint retain their
release identities. Published source delivery, provider provenance, versioned
settings, recovery records and reviewed template versions have explicit readers.
Internal modules belong to the implementation. Engineering snapshots are pinned to
`serrja/pythonTemplate` in [engineering source metadata](../.github/engineering-source.json).
