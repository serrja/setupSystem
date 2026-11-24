# Development

Use a normal account with Git, GNU Make, GNU gzip, dpkg-deb, util-linux, the sudo package for its offline policy checkers, and uv. Run development commands from the repository root. CI exercises Python 3.14.7 and the supported minimum Python 3.12. The engineering dependency closure is recorded in `uv.lock`.

```sh
uv sync --locked --all-groups --python 3.14.7
make run
make native-check
make native-ci
```

Use `make package` to build a Debian archive without sudo. `make install` is a separate package transaction. `make update` fetches the stable Debian release through the same verifier used by the HTTPS delivery command. Never install the system package or apply workstation changes on the development host as a test.

The native gate runs Ruff, mypy, ShellCheck, Ansible checks and pytest. Coverage includes the application, Ansible helpers and filters, packaged executable assets and owned build tools. The combined statement and branch coverage floor is 75%. JUnit reports skipped cases separately from passing tests. Packaging compares the Make, Ansible, config and asset bytes in the wheel and source distribution, then exercises the installed wheel from a temporary directory. Tests use synthetic HOME and XDG paths for writes. The CI builder also checks the allocated wheel outside the source checkout.

The engineering upstream is [pythonTemplate](https://github.com/serrja/pythonTemplate). The reviewed revisions are recorded in [engineering-source.json](https://github.com/serrja/setupSystem/blob/main/.github/engineering-source.json). Changes to upstream-owned release helpers require a reviewed upstream revision.

Use a separately authorized disposable Debian machine for install, upgrade, remove, compositor and boot acceptance. Keep its release tag, source commit, architecture, installed package versions, configuration checksums and recovery observations with the test evidence. See [testing](testing.md).
