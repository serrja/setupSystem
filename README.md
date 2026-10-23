# setupSystem

One installer for Debian workstations, portable user profiles and pinned project templates.
General OS setup supports Debian 12/13 on amd64/arm64; terminalDev archives currently
support amd64. The optional Hyprland profile targets Debian 13 with official backports
and Hyprland 0.55.2. Portable fonts can be applied separately on other Linux hosts.
Prepared desktop files are not evidence of a successful graphical login.

```bash
python3 bootstrap.py plan
```

Start with [Linux](docs/linux.md) → [Debian installation](docs/debian.md) →
[configuration](docs/debian.md#configuration). Every write needs explicit apply;
startup never installs or upgrades software.

- [Hyprland and keys](docs/hyprland.md) · [Fonts](docs/fonts.md)
- [Terminal](docs/terminalGuide.md) · [Neovim](docs/neovimGuide.md)
- [Recovery](docs/recovery.md) · [Project templates](docs/templates.md)
- [Development](docs/development.md) · [Versions](docs/versioning.md)
- [Workspace boundaries](docs/workspaceGuide.md) · [Happ](docs/happGuide.md)

New work lives in `dev`: `make lint`, review the diff, `git push origin dev`.
Full acceptance is manual. `main` remains the previously accepted version.
