# Development

Build and test as a normal account from the Git root. Install Git, GNU Make, GNU gzip, dpkg-deb, util-linux, the sudo package for its offline policy checkers, and uv. CI exercises Python 3.14.7 and the supported minimum Python 3.12. The engineering dependency closure is recorded in `uv.lock`.

## 🧪 Run the local checks

```sh
uv sync --locked --all-groups --python 3.14.7
make run
make native-check
make native-ci
```

Use `make package` to build a Debian archive without sudo. `make install` is a separate package transaction. `make update` fetches the stable Debian release through the same verifier used by the HTTPS delivery command. Never install the system package or apply workstation changes on the development host as a test.

The native gate runs Ruff, mypy, compile and import checks, ShellCheck, Ansible lint and syntax checks, manifest validation and Alembic schema comparison. Artifact verification compares wheel, source distribution and Debian payloads and opens an installed schema outside the checkout. The product currently has no automated behavioural cases. The owner withdrew the suite and its coverage threshold. `make test` reports this explicitly. Keep manual observations and target acceptance results separate from build evidence.

## Change a setting

For a UI timing or scan limit, declare the typed field and its bounds in `setup_system/settings.py` and the default in `config/application.toml`. Inject the validated setting at its consumer. Only normal-user application fields are eligible for environment overrides. A program setting belongs in its plugin Pydantic model. Regenerate its manifest and offline reference. Exercise the field in an isolated environment and record the observed behaviour. Build the catalog to exercise activation from source.

```sh
uv run --locked python docker/settings_reference.py --write
uv run --locked python docker/plugin_reference.py --write
uv run --locked python docker/plugin_artifacts.py --output coverage/plugin_catalog --with-runtime
make native-ci
```

The Debian builder reads committed Git blobs. Run the packaging gate on the candidate commit to compare its modules, resources and offline guides across all three archives.

## 🧬 Engineering provenance

The engineering upstream is [pythonTemplate](https://github.com/serrja/pythonTemplate). The reviewed revisions are recorded in [engineering-source.json](https://github.com/serrja/setupSystem/blob/main/.github/engineering-source.json). Changes to upstream-owned release helpers require a reviewed upstream revision.

Use a separately authorized disposable Debian machine for install, upgrade, remove, compositor and boot acceptance. Keep its release tag, source commit, architecture, installed package versions, configuration checksums and recovery observations with the test evidence. See [testing](testing.md).
