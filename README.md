# setupSystem

A Textual application for configuring an **existing minimal Debian 13 (trixie)** machine
with Hyprland and a selected working environment. Supported target architectures are
amd64 and arm64. It does not create installation media or repartition disks.

## 🚀 Open the application

```sh
setup-system
```

Run as your normal account. Opening, navigating and planning make no changes. Switch
freely between **Base system** and **Working environment**. Save intentions independently
of installation, review a fixed plan and confirm Apply. Sudo is requested only when the
selected action needs system changes; the Python UI never runs as root.

For first delivery, obtain `install.sh` from a published, verified release and use its
actual tag:

```sh
sh install.sh --release vX.Y.Z
```

`vX.Y.Z` is a placeholder, not a claimed release. The bootstrap verifies release/runtime
inputs, prepares the locked runtime as your normal user and opens the same Textual UI.
It contains no Hyprland or APT installation logic. An unpublished checkout does not
provide release provenance for privileged Apply. No manual Python/uv setup is required
for release delivery.

## 🎨 Configure and recover

The shared default is **Catppuccin Mocha Green**, with a floating top Waybar, Foot,
Wofi, Mako, Hyprlock, greetd and an offline Neovim color fallback. Panel compactness,
opacity, rounding and effects are adjustable without reinstalling packages. Existing
Lua workspace IPC, hardware detection and preferred monitor/automatic scaling remain.

Applications and cleanup are optional. Happ is independent of base installation;
account setup belongs to each application. Unsupported or unverifiable downloads show
`UNAVAILABLE`. Cleanup simulates and rechecks the exact removal closure, with no implicit
purge/autoremove. Existing files require ownership evidence before replacement.

System Apply uses signed official Debian repositories/backports and independently
verified, root-owned release resources. User Ansible roles run without privilege.
Operations keep their plan when tabs change; cancellation waits for a safe boundary and
reports completed stages. No automatic reboot, SSH interruption or encryption conversion.
GRUB keeps a visible recovery menu.

## 📚 Documentation

- [Architecture](docs/architecture.md) · [Textual workflow](docs/textual_workflow.md)
- [Configuration](docs/configuration.md) · [Theme](docs/theme.md)
- [Debian and Hyprland](docs/debian.md) · [Recovery](docs/recovery.md)
- [Applications](docs/applications.md) · [Neovim](docs/neovim_guide.md)
- [Graphical unlock](docs/graphical_unlock.md) · [Releases](docs/release_guide.md)
- [Development](docs/development.md) · [Contributing](CONTRIBUTING.md)

Automated evidence covers mocks, private APT simulations, headless/PTY Textual and
installed artifacts. Physical installation, boot/unlock, GPU, audio, panels and browser
GUI acceptance require a separately selected disposable Debian machine.
