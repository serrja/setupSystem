"""Read-only planning shared by the stdlib bootstrap and the Typer CLI."""

from __future__ import annotations

import hashlib
import os
import platform as system_platform
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Platform:
    distribution: str
    version: str
    architecture: str
    implemented: bool
    verified: bool
    reason: str


@dataclass(frozen=True)
class Step:
    id: str
    resources: tuple[str, ...]
    dependencies: tuple[str, ...]
    reason: str
    risk: str
    privilege: str
    command: str


@dataclass(frozen=True)
class Plan:
    platform: Platform
    steps: tuple[Step, ...]
    observed: tuple[tuple[str, str], ...]
    blocked: tuple[str, ...] = ()


def detect_platform(
    os_release: Path = Path("/etc/os-release"),
    architecture: str | None = None,
    system_name: str | None = None,
) -> Platform:
    family = system_name or system_platform.system()
    arch = architecture or system_platform.machine()
    if arch == "aarch64":
        arch = "arm64"
    if family != "Linux":
        return Platform(family.lower(), "unknown", arch, False, False, "UNSUPPORTED platform")
    fields: dict[str, str] = {}
    try:
        for line in os_release.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                fields[key] = value.strip().strip('"')
    except OSError:
        pass
    distro = fields.get("ID", "unknown")
    version = fields.get("VERSION_ID", "unknown")
    supported = (
        distro == "debian"
        and version in {"12", "13"}
        and arch
        in {
            "x86_64",
            "arm64",
        }
    )
    reason = (
        f"{distro.title()} {version} {arch} explicit platform adapter"
        if supported
        else ("UNSUPPORTED: only explicit Debian 12/13 on x86_64 or arm64 is implemented")
    )
    return Platform(distro, version, arch, supported, False, reason)


def _digest(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if not path.exists():
        return "absent"
    if not path.is_file():
        return "non-file"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_plan(root: Path, profile: str = "base") -> Plan:
    if profile not in {"base", "terminalDev", "happ"}:
        raise ValueError("Unknown setup profile")
    platform = detect_platform()
    if not platform.implemented:
        return Plan(platform, (), ())
    if profile == "terminalDev" and platform.architecture == "arm64":
        return Plan(
            platform,
            (),
            (),
            ("terminalDev Linux arm64 archives and parser toolchain are not pinned",),
        )
    home = Path.home()
    xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    xdg_state = Path(os.environ.get("XDG_STATE_HOME", home / ".local/state"))
    codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex"))
    targets = (
        home / ".zshrc",
        home / ".bashrc",
        home / ".gitconfig",
        codex_home / "AGENTS.md",
        codex_home / "config.toml",
        xdg_config / "zsh/init.zsh",
        xdg_state / "zsh",
        xdg_config / "git/setup-system.inc",
    )
    observed = tuple((str(path), _digest(path)) for path in targets)
    steps: tuple[Step, ...] = (
        Step(
            "agents.assets",
            ("~/.agents/policy", "~/.agents/skills"),
            (),
            "Install short shared rules and scoped skills",
            "user files",
            "user",
            "copy managed assets",
        ),
        Step(
            "agents.codex",
            ("$CODEX_HOME/AGENTS.md", "$CODEX_HOME/config.toml"),
            ("agents.assets",),
            "Connect supported Codex discovery and default read-only profile",
            "Codex settings",
            "user",
            "merge configuration",
        ),
        Step(
            "zsh.assets",
            ("$XDG_CONFIG_HOME/zsh", "$XDG_STATE_HOME/zsh"),
            (),
            "Install shell-only startup modules",
            "user files",
            "user",
            "copy managed assets",
        ),
        Step(
            "zsh.rc",
            ("~/.zshrc", "~/.bashrc"),
            ("zsh.assets",),
            "Use managed startup and remove the old Bash handoff",
            "shell startup",
            "user",
            "replace owned blocks",
        ),
        Step(
            "git.include",
            ("~/.gitconfig", "$XDG_CONFIG_HOME/git/setup-system.inc"),
            (),
            "Keep Git styling independent of checkout",
            "Git settings",
            "user",
            "merge owned include",
        ),
        Step(
            "codex.completion",
            ("$XDG_CACHE_HOME/setup-system/codex.zsh",),
            ("zsh.assets",),
            "Generate completion once during apply",
            "cache file",
            "user",
            "codex completion zsh",
        ),
    )
    if profile == "terminalDev":
        steps += (
            Step(
                "terminalDev.tools",
                ("$XDG_DATA_HOME/setup-system/neovim", "$XDG_DATA_HOME/nvim/lazy"),
                (),
                "Install pinned Neovim and plugins from verified upstream commits",
                "network and executable code",
                "user",
                "setup-system apply --profile terminalDev --yes",
            ),
            Step(
                "terminalDev.config",
                ("$XDG_CONFIG_HOME/nvim", "$XDG_CONFIG_HOME/tmux"),
                ("terminalDev.tools",),
                "Install editor and tmux configuration with private backup",
                "user files",
                "user",
                "copy managed assets",
            ),
        )
    if profile == "happ":
        steps += (
            Step(
                "happ.package",
                ("official Happ desktop package",),
                (),
                "Preserve installed Happ; otherwise verify the pinned official release",
                "system package and possible desktop permissions",
                "targeted sudo on Debian only",
                "setup-system happ setup --yes",
            ),
            Step(
                "happ.nativeAutostart",
                ("Happ application setting and login registration",),
                ("happ.package",),
                "Read back native autostart; UI action is required where no supported API exists",
                "login behavior",
                "user",
                "setup-system happ configureAutostart",
            ),
        )
    return Plan(platform, steps, observed)


def format_plan(plan: Plan) -> str:
    p = plan.platform
    lines = [f"Platform: {p.distribution} {p.version} {p.architecture} — {p.reason}"]
    if not p.implemented:
        lines.append("No installation, backup, download, sudo, or configuration step is available.")
        return "\n".join(lines)
    if plan.blocked:
        lines.extend(f"BLOCKED: {reason}" for reason in plan.blocked)
        return "\n".join(lines)
    lines.append("plan is read-only; apply always rechecks current state.")
    for step in plan.steps:
        lines.append(
            f"{step.id}: {step.command}; resources={', '.join(step.resources)}; "
            f"depends={', '.join(step.dependencies) or '-'}; privilege={step.privilege}; "
            f"risk={step.risk}; reason={step.reason}"
        )
    return "\n".join(lines)
