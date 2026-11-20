# Debian target

The workstation target is an existing minimal Debian 13 (trixie), amd64 or arm64,
without another desktop. Debian 12 portable capabilities do not imply Hyprland support.
Unknown distributions, versions and architectures stop before system changes.

`manifests/platforms.toml` owns package revisions, architecture aliases and capabilities.
Hyprland and its matching portal/idle/lock dependencies come from signed official
trixie-backports; stable/security packages remain official Debian sources. Resolve
actual availability and simulate before applying. No testing/unstable source, silent
build, dist-upgrade or desktop removal is used.

APT acquisition uses isolated signed sources and direct HTTPS with TLS verification.
An exact dependency closure is downloaded, then applied without network inside the
existing offline namespace adapter. Services remain deferred to preserve current
network/SSH and sessions. Package installation is not transactionally reversible.

See [official backports instructions](https://backports.debian.org/Instructions/),
[Hyprland](hyprland.md), [login](desktop_login.md) and [recovery](recovery.md).

## Regional settings

Base system can apply timezone, locale and keyboard independently. An empty field preserves the current value. Timezone changes use an installed IANA zone and verify Debian's active timezone. Locale selection uses Debian's UTF-8 locales. When the selected locale needs generation, the operation backs up its selection file and runs `locale-gen --keep-existing`. The plan lists missing console-setup or locales prerequisites before Apply. The keyboard action preserves unrelated XKB settings in `/etc/default/keyboard` and prepares the console cache through `setupcon --keyboard-only --save-only`. The new mapping takes effect for subsequent console sessions. Each operation compares its observed value again before applying and verifies its result. Recovery metadata and previous configuration bytes are under `/var/lib/setup-system/regional/`.

The normal user's installed application and the sudo authorization prompt are trusted. The isolated system interpreter validates a published setupSystem release tag and its commit against GitHub, obtains the platform matrix and stdlib bootstrap from that commit, then verifies the release archive against its checksums and native build receipt. The root adapter imports the verified root-owned source. The system boundary protects normal operation under a trusted caller. General sudo access gives its holder full administrative authority.
