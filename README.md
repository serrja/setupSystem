# 🧩 setupSystem

setupSystem is a Textual application for reviewing and applying workstation changes as a normal user. Install or activate the plugins you need. Each plugin owns its settings, actions and offline Help.

## 🚀 Quick start

From the checkout with uv and Python 3.12 or later, start the host.

```sh
make prepare
make run
```

Select a plugin, preview its plan, then confirm the executor, permissions and resource changes. An empty catalog can be populated with a reviewed release descriptor. [Configuration](docs/configuration.md) · [Using the configurator](docs/textual_workflow.md) · [Debian installation](plugins/debian/docs/install.md)

## 🎯 Scope

The host manages confirmations, worker generations and the SQLite ledger. Recovery depends on retained receipts and the availability of the corresponding executor. Machine and boot acceptance require a disposable target. [Architecture](docs/architecture.md) · [Database](docs/database.md) · [Recovery](docs/recovery.md)

## 🧩 Plugins

Browse the [application catalog](docs/applications.md) or [write a plugin](docs/plugin_authoring.md). Development commands and the test infrastructure policy are in the [contributor guide](CONTRIBUTING.md).

## 📄 License

The project uses the [MIT license](LICENSE.md). See the [third-party notices](THIRD_PARTY_NOTICES.md).
