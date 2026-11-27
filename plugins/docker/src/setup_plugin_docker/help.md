# 🧩 Docker

Docker workstation plugin.

## ⚙️ Daemons and contexts

Select rootless or rootful in Settings, then open Actions and review the daemon mode. Choose an existing local context or use the mode’s default. The preview shows the service, socket, context and Engine ID. Apply installs the private launcher `~/.local/bin/setup-system-docker`. Run `~/.local/bin/setup-system-docker info` to use the selected Engine. The launcher reads its selection from `$XDG_CONFIG_HOME/setup-system/docker/selected-daemon.json` (default `~/.config/setup-system/docker/selected-daemon.json`). Each invocation checks the client context endpoint, server identity and daemon security mode. Select another endpoint through the mode action. Global `--host` and `--context` overrides are rejected before the Docker subcommand. Changing the global Docker context, registry configuration or group membership requires a separate decision. Rollback restores the previous launcher from its file receipt. [Docker contexts](https://docs.docker.com/engine/manage-resources/contexts/) · [Docker CLI options](https://docs.docker.com/reference/cli/docker/).

A running local systemd service and a matching Unix socket are required. If the rootless service is absent, review the reported UID/GID helper and subordinate ID requirements. The packaged `dockerd-rootless-setuptool.sh install` runs as your user and may create a service, a client context and daemon state. Run it from a terminal after reviewing those changes. Review Start separately for an inactive service. Ask your administrator about an unavailable rootful service or inaccessible socket. Access to the rootful Engine grants root-equivalent control. [Rootless Docker](https://docs.docker.com/engine/security/rootless/).

## 🔐 Registry accounts

Configure a working pass or secretservice store for the selected registry and choose Registry login. The native Docker client opens in a separate terminal with that single credential helper. After login, the action checks the helper entry for the selected server. An existing local entry can survive remote revocation. Sign out before signing in with another account. If the terminal is unavailable, use the displayed Docker command with the same configured helper. [Docker login](https://docs.docker.com/reference/cli/docker/login/).

## 🧹 Data

Volumes, images and running containers remain user data. Client-context creation does not connect to its socket.
