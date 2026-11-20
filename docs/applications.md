# Applications

Every application is an explicit Workspace selection. Catalog presence does not establish
installation. Native package metadata, binary version or readback establishes actual state.

| Application | Delivery and limits |
| --- | --- |
| Neovim | Existing pinned upstream archive/plugins; normal user only, offline native theme fallback |
| Codex CLI | Official pinned release archive and SHA-256 for amd64/arm64; preserve configured model and auth store |
| Firefox ESR | Debian package, Strict tracking protection, HTTPS-Only, official AMO uBlock Origin; preserve profiles |
| Happ | Optional pinned upstream `.deb`, verified digest/metadata and native helper; native account/import UI |
| ChatGPT Desktop | Official Debian 13 Linux preview exists; download returned HTTP 403 during verification, so unavailable until a reviewed version/checksum pin exists |
| Git / gh / shell tools | Selected Debian packages and native account tools |
| Other applications / language profiles | Only available when their delivery adapter and platform provenance are verified |

Codex defaults use workspace-write and on-request supported by the pinned version.
No global bypass, auth.json backup or additional AI editor plugin is installed. Model
selection remains in the user's existing configuration. High reasoning is selected only
when public cached model capability metadata confirms support; no model is invented. Existing permission profiles
require explicit review instead of automatic conversion.

Firefox preserves existing enterprise policies if they conflict. Its package and policy
file readback do not establish a running browser's `about:policies` or extension state;
validate those in a disposable graphical session. Safe updates, malicious-site protection
and TLS remain enabled. No mandatory DoH or Happ dependency is introduced.

Official references: [Codex CLI](https://learn.chatgpt.com/docs/codex/cli.md),
[ChatGPT Linux](https://learn.chatgpt.com/docs/linux/linux-app.md),
[Happ source](https://github.com/Happ-proxy/happ-desktop/releases/tag/4.5.4).
