# Development

Run from the exact repository root. Native checks use uv 0.12.15, locked dependencies
and Python 3.14.7; CI additionally checks the CLI's Python 3.12 minimum. They need no
Docker, sudo, GitHub credentials or graphical session. The isolated resolver regressions
require `apt-get` and `apt-cache`; they use private cached indexes and simulated plans,
without network access, package installation or host APT state changes. Source-access
probes and user operations reuse the running application interpreter; the Debian quality image
does not install a separate system Python.
The locked development and application runtime remains Python 3.14.7.

```bash
uv python install 3.14.7
uv sync --locked --all-groups --python 3.14.7
make help
make native-check
make native-ci
```

`native-check` runs `make CONTAINERIZED=1 check`: Ruff check/format, strict mypy,
compilation, lock validation, ShellCheck, Ansible lint/syntax, read-only entrypoint
checks and the full fixture suite. Ansible scratch data stays under ignored coverage/.
Ansible-lint has a transitive Black dependency; project Python formatting still uses Ruff.
Tests run once per supported runtime in CI. `native-build` creates one wheel and one
sdist under `coverage/native-artifacts/`; `native-verify` installs the wheel and locked
runtime dependencies into a disposable environment outside the source checkout.
It checks metadata, noneditable wheel provenance, resources and the public Textual entrypoint and headless navigation.
`native-ci` runs those three targets in order. After changing the package version,
choose a fresh `NATIVE_ARTIFACT_DIR` if the previous directory contains older artifacts.

The isolated build backend and its complete dependency closure are version-pinned
through root `tool.uv.build-constraint-dependencies`, matching `uv.lock`. uv 0.12.15
records these constraints in the lock and applies them during build dependency
resolution. Its isolated builds do not enforce the development lock's archive hashes;
do not confuse backend version constraints with runtime hash verification. Native
release builds use the already prepared locked backend with build isolation disabled.

For a narrow edit, run locked Ruff and the affected pytest module before the full
gate. [Testing](testing.md) explains fixture, installed-artifact and GUI evidence.
`test-integration` runs the real Copier update and native release build fixtures;
it does not execute the workstation installer on the host.

## 🐳 Docker equivalent

Git, Make, Docker and Buildx can run the existing pinned Debian quality graph:

```bash
make ci-local
make coverage
make release-check
```

The full graph runs source checks once, reports coverage without rerunning tests,
builds canonical distributions and checks the installed CLI outside source. Reports
under ignored `coverage/` bind source identity, tools, commands, exit codes and
artifact checksums. Below 100% coverage is advisory; failed assertions, types,
builds and missing/corrupt/stale receipts remain errors. No Docker resource is pruned.

After preparing the same exact source online, `OFFLINE=1 make ci-local` checks cached
image identities/artifact hashes and reruns checks with no network or rebuild.
Unavailable or changed inputs fail explicitly. The first dependency/image download
needs network access. Native and Docker gates provide code/package evidence, not a
Debian installation or working GPU/audio/portal session.

Managed `docker/release.py` and `docker/python_release.py` retain their reviewed owner's
88-column formatting; Make checks that separately. Source/provenance pins live in
`.github/engineering-source.json`. Refresh them only after reviewing the upstream diff.
See [versioning](versioning.md) and [releases](release_guide.md) before promotion.
