# ChatGPT Desktop

The [official Linux preview](https://learn.chatgpt.com/docs/linux/linux-app.md) supports
Debian 13 on x64 and ARM64. It normally uses XWayland; native Wayland is experimental.
Do not disable the application sandbox or replace it with an unofficial wrapper.

The official package endpoints returned HTTP 403 during source verification. A stable
version/checksum/publisher provenance pin could not be established, so Workspace reports
`UNAVAILABLE` and performs no installation for this selection. A reviewed package adapter
and disposable GUI test are required before enabling delivery. A live `latest` URL alone
is not a reproducible package pin.

Account authorization belongs to ChatGPT's own interface. setupSystem does not collect
credentials or copy application authorization state. See [applications](applications.md).
