# 🧩 setupSystem

setupSystem is a Textual application for reviewing and applying workstation changes as a normal user. Install or activate the plugins you need. Each plugin owns its settings, actions and offline Help.

## 📦 Install the Debian integration

On Debian 13 (trixie), amd64 or arm64, download [install.sh](https://raw.githubusercontent.com/serrja/setupSystem/main/plugins/debian/packaging/install.sh) and install the latest verified stable release as a normal user:

```sh
curl -fsSL https://raw.githubusercontent.com/serrja/setupSystem/main/plugins/debian/packaging/install.sh -o install.sh && sh install.sh
```

Start the application separately:

```sh
setup-system
```

For prerequisites, root-password authentication, updates and the bootstrap trust model, see the [Debian installation guide](plugins/debian/docs/install.md).

The verified Debian package bundles uv and locked Python wheels. uv prepares the app and bundled plugins; APT installs only system prerequisites and the verified `.deb`. Activation of a bundled plugin does not download uv again.

## 🔄 Update

As a normal user, download the current verified loader and update to the latest stable release:

```sh
curl -fsSL https://raw.githubusercontent.com/serrja/setupSystem/main/plugins/debian/packaging/install.sh -o install.sh && sh install.sh update
```

Open the application separately with `setup-system` after the update.

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
