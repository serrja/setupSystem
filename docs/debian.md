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
