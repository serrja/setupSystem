# setupSystem

setupSystem is a Textual configurator for Debian 13 workstations on amd64 and arm64. Choose a program or desktop component, preview an individual action and review its exact resources before applying it.

## 📦 Install and launch

Run the verified release loader as your normal user. APT installs the configurator and its dependencies. Open the application separately after installation. [Delivery and prerequisites](docs/debian.md)

```sh
sh -c 'loader=$(curl -qfsSL --proto =https --proto-redir =https "$1") && exec /bin/sh -c "$loader" install.sh install' sh \
  https://raw.githubusercontent.com/serrja/setupSystem/main/install.sh
```

Start the configurator as your normal user in a terminal or a text console (TTY).

```sh
setup-system
```

You can also open **setupSystem** from the desktop applications menu. For an installed package, run `make -C /usr/lib/setup-system update`. The source checkout supports `make run`.

## Using the configurator

Open an extension and review its source, digest and permissions before activation. Use **Settings** to save its preferences. Choose **Preview** under **Actions** to inspect the proposed changes. Telegram Desktop is available in the **Snap** catalog. [Workstation workflow](docs/textual_workflow.md) · [Applications](docs/applications.md)

For an interrupted action or an upgrade from an older release, keep the existing state directory and consult the recovery guide. [Ownership and recovery](docs/recovery.md) · [Database and migrations](docs/database.md)

See the [plugin author guide](docs/plugin_authoring.md), [configuration reference](docs/settings_map.md), [development gates](docs/development.md) and [release procedure](docs/release_guide.md).
