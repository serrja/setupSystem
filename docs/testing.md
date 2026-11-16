# Testing and delivery

Run `make native-check` after preparing the locked environment; `make native-ci`
adds wheel/sdist build and isolated installed acceptance. The pinned Docker equivalent
is `make ci-local`. [Development](development.md) owns preparation commands.

## 🧪 Scope of evidence

Source tests use synthetic HOME/XDG directories, temporary Git repositories, fixtures
and mock executors. They cover unsupported platform/user arguments before writes,
read-only plan, command failures, ownership boundaries, conflict preservation,
backup/recovery and reruns. They never select the developer host as an installation
target, invoke an actual system apply, or mount a real HOME/device into a test guest.

Real Copier copy/update tests use annotated Git fixtures offline, preserve owner
code/settings/migrations, reject unsafe origins/conflicts and suppress template tasks.
Native release tests build real Hatchling wheels/sdists in an exact Git export,
verify allocated dev/stable metadata, reject mismatched source/attempt/receipt and
confirm the original project/lock stay unchanged. They do not allocate real releases.
The actual inline contribution guard is exercised with accepted and rejected branch
promotions, fork identities and invalid titles.

`native-verify` installs the exact wheel with locked runtime requirements in a new
external virtual environment and checks metadata, PEP 610 noneditable provenance,
bundled manifests/resources and CLI commands. The H02 builder checks the allocated
version before attestation; provider verification independently inspects archive
bytes without executing consumer code. Unit/mock evidence is distinct from a hosted
run, published byte audit or interrupted-draft recovery.

The source suite runs once per promised Python runtime in CI and produces statement
and branch coverage. Coverage below 100% is advisory. Assertions, types, malformed
configurations, build failures and missing/stale reports fail. Report unavailable
network, Docker or guest capabilities explicitly; do not turn them into success.

## 🖥️ Installation and session smoke

A real installer smoke requires an explicitly selected disposable copy of the
owner's clean Debian system, verified guest identity/codename/architecture and no
passthrough of working HOME/disks. Run the public one-command entrypoint and repeat
it there; record actual ownership, files, APT/source plan and recovery results.
A container or mock executor does not prove this installation or graphical behavior.

Check login/TTY return, renderer/GPU, audio, screen sharing, lock/resume and network
preservation in a real session. If no approved disposable guest is available, state
`REAL INSTALL/GUI SMOKE NOT RUN`; successful unit tests or package builds do not
replace that evidence. Never start a compositor, reboot or interrupt an existing
session as part of ordinary source CI.
