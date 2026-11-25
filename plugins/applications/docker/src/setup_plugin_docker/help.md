# Docker

## ⚙️ Daemons and contexts

Check the rootless prerequisites and the selected Unix socket before creating a client context. Start and Stop act on the user service for rootless mode and can affect running containers. Inspect rootful service settings with your administrator. [Rootless Docker](https://docs.docker.com/engine/security/rootless/) · [Docker contexts](https://docs.docker.com/engine/manage-resources/contexts/).

## 🔐 Registry accounts

Configure the platform credential helper before invoking the native registry login. The client can otherwise store credentials in its local config. [Docker login](https://docs.docker.com/reference/cli/docker/login/).

## 🧹 Data

Volumes, images and running containers remain user data. Client-context creation does not connect to its socket.
