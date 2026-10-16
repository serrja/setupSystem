"""Typer interface for workstation setup."""

from __future__ import annotations

import getpass
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Annotated

import typer

from setup_system.application.apps import appStatus, catalog, installZsh
from setup_system.application.configuration import applyConfiguration
from setup_system.application.happ import (
    backupHapp,
    classifyLink,
    clearLinkClipboard,
    clipboardCommand,
    copyLinkToClipboard,
    formatHappStatus,
    inspectHapp,
    restoreHapp,
    setupHapp,
)
from setup_system.application.terminalDev import (
    installArchiveTool,
    installLanguageTools,
    installNeovim,
    installParsers,
    installPlugins,
    installSystemTools,
)
from setup_system.planning import createPlan, formatPlan
from setup_system.storage import newBackup
from setup_system.storage import restore as restoreBackup

SOURCE_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ROOT = SOURCE_ROOT if (SOURCE_ROOT / "manifests/platforms.toml").is_file() else PACKAGE_ROOT
app = typer.Typer(help="Debian workstation setup and recovery.", no_args_is_help=True)
appsApp = typer.Typer(help="Applications and installation sources.")
agentsApp = typer.Typer(help="Scoped agent configuration.")
templatesApp = typer.Typer(help="Generic project templates.")
projectsApp = typer.Typer(help="Open one discovered project root.")
happApp = typer.Typer(help="Happ desktop package and private-state operations.")
app.add_typer(appsApp, name="apps")
app.add_typer(agentsApp, name="agents")
app.add_typer(templatesApp, name="templates")
app.add_typer(projectsApp, name="projects")
app.add_typer(happApp, name="happ")


@app.command()
def plan(profile: Annotated[str, typer.Option(help="base, terminalDev or happ.")] = "base") -> None:
    """Show a read-only plan, including unsupported-platform status."""
    result = createPlan(ROOT, profile)
    typer.echo(formatPlan(result))
    if not result.platform.implemented or result.blocked:
        raise typer.Exit(4)


@app.command()
def apply(
    yes: Annotated[
        bool, typer.Option("--yes", help="Apply the current user configuration plan.")
    ] = False,
    profile: Annotated[str, typer.Option(help="base, terminalDev or happ.")] = "base",
) -> None:
    """Apply the freshly checked user configuration plan."""
    result = createPlan(ROOT, profile)
    if not result.platform.implemented or result.blocked:
        typer.echo(formatPlan(result), err=True)
        raise typer.Exit(4)
    if not yes:
        typer.echo(formatPlan(result))
        if not typer.confirm("Apply user configuration?", default=False):
            raise typer.Exit(2)
    try:
        backup, statuses = applyConfiguration(ROOT, terminalDev=profile == "terminalDev")
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        typer.echo(f"failed: {exc}", err=True)
        raise typer.Exit(5) from exc
    typer.echo(f"backup: {backup}")
    for path, status in statuses:
        typer.echo(f"{status}: {path}")
    if profile == "terminalDev":
        try:
            typer.echo(installNeovim(ROOT, result.platform))
            for status in installPlugins(ROOT):
                typer.echo(status)
            for toolName in ("stylua", "tree-sitter"):
                typer.echo(installArchiveTool(ROOT, result.platform, toolName))
            for status in installLanguageTools(ROOT):
                typer.echo(status)
            typer.echo(installParsers(ROOT))
            toolStatus, blocked = installSystemTools(ROOT, result.platform)
            typer.echo(toolStatus)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            typer.echo(f"terminalDev failed: {exc}", err=True)
            raise typer.Exit(5) from exc
        if blocked:
            raise typer.Exit(4)
    if profile == "happ":
        try:
            typer.echo(setupHapp(ROOT, result.platform))
            typer.echo(formatHappStatus(inspectHapp(result.platform)))
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            typer.echo(f"Happ setup failed: {exc}", err=True)
            raise typer.Exit(5) from exc


@app.command()
def doctor(
    profile: Annotated[str, typer.Option(help="base, terminalDev or happ.")] = "base",
) -> None:
    """Inspect the active host without changing it."""
    result = createPlan(ROOT, profile)
    typer.echo(formatPlan(result).splitlines()[0])
    for name, entry in catalog(ROOT).items():
        typer.echo(f"{name}: {appStatus(entry)}")
    for name in ("docker", "zsh", "codex", "uv"):
        typer.echo(f"command {name}: {shutil.which(name) or 'absent'}")
    if profile == "terminalDev":
        for name in (
            "nvim",
            "rg",
            "fzf",
            "fdfind",
            "fd",
            "tmux",
            "clangd",
            "basedpyright-langserver",
            "ruff",
            "stylua",
            "cmake",
            "ninja",
        ):
            typer.echo(f"command {name}: {shutil.which(name) or 'absent'}")
        data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        typer.echo(
            f"lazy.nvim: {'present' if (data / 'nvim/lazy/lazy.nvim').is_dir() else 'missing'}"
        )
    if profile == "happ":
        typer.echo(formatHappStatus(inspectHapp(result.platform)))
    if not result.platform.implemented or result.blocked:
        raise typer.Exit(4)


@app.command()
def backup(
    includeVault: Annotated[
        bool, typer.Option("--include-vault", help="Also capture the selected Obsidian vault.")
    ] = False,
    vault: Annotated[
        Path | None, typer.Option(help="Existing vault path when --include-vault is set.")
    ] = None,
    profile: Annotated[str, typer.Option(help="base, terminalDev or happ.")] = "base",
) -> None:
    """Create a private hash-checked backup of user configuration."""
    if profile == "happ":
        try:
            typer.echo(str(backupHapp()))
        except (OSError, ValueError, RuntimeError) as exc:
            typer.echo(f"Happ backup blocked: {exc}", err=True)
            raise typer.Exit(4) from exc
        return
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    codexHome = Path(os.environ.get("CODEX_HOME", home / ".codex"))
    files = [
        home / ".zshrc",
        home / ".bashrc",
        home / ".gitconfig",
        codexHome / "config.toml",
        codexHome / "AGENTS.md",
    ]
    files += list((config / "zsh").glob("*.zsh")) + list((config / "git").glob("*.inc"))
    files += list((home / ".agents").rglob("*.md"))
    if profile == "terminalDev":
        files += [item for item in (config / "nvim").rglob("*") if item.is_file()]
        files += [item for item in (config / "tmux").rglob("*") if item.is_file()]
    if includeVault:
        source = vault or home / "Obsidian Vault/Хранилище"
        if not source.is_dir():
            typer.echo("Vault path does not exist; specify --vault.", err=True)
            raise typer.Exit(2)
        for candidate in source.rglob("*"):
            if candidate.suffix.lower() in {".sqlite", ".sqlite3", ".db", ".wal", ".shm"}:
                typer.echo("Live database detected; use its native backup command.", err=True)
                raise typer.Exit(4)
            if candidate.is_file():
                files.append(candidate)
    try:
        folder = newBackup(files)
    except (OSError, ValueError) as exc:
        typer.echo(f"backup failed: {exc}", err=True)
        raise typer.Exit(5) from exc
    typer.echo(str(folder))


@app.command()
def restore(
    backupId: Annotated[
        str, typer.Option("--backup", help="Backup ID from the private backup directory.")
    ],
    dryRun: Annotated[
        bool, typer.Option("--dry-run", help="Show changes without writing (default).")
    ] = True,
    applyChanges: Annotated[bool, typer.Option("--apply", help="Restore verified files.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm non-interactive restore.")] = False,
    replaceCurrent: Annotated[
        bool,
        typer.Option("--replaceCurrent", help="Back up and replace conflicting current files."),
    ] = False,
) -> None:
    """Verify a backup and optionally restore its files."""
    if applyChanges:
        platformAdapter = createPlan(ROOT).platform
        if not platformAdapter.implemented:
            typer.echo(platformAdapter.reason, err=True)
            raise typer.Exit(4)
    if applyChanges and not yes:
        typer.echo("Restore requires --apply --yes.", err=True)
        raise typer.Exit(2)
    if replaceCurrent and not applyChanges:
        typer.echo("--replaceCurrent requires --apply --yes.", err=True)
        raise typer.Exit(2)
    try:
        statuses = restoreBackup(backupId, apply=applyChanges, replaceCurrent=replaceCurrent)
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"restore failed: {exc}", err=True)
        raise typer.Exit(5) from exc
    for path, status in statuses:
        typer.echo(f"{status}: {path}")
    if applyChanges and any(status == "conflict" for _, status in statuses):
        typer.echo(
            "Conflicts preserved; use --replaceCurrent only after reviewing dry-run.", err=True
        )
        raise typer.Exit(4)
    if dryRun and not applyChanges:
        typer.echo("dry-run: no files changed")


@happApp.command("doctor")
def happDoctor() -> None:
    """Read safe package, native-autostart and routing categories only."""
    platformAdapter = createPlan(ROOT, "happ").platform
    typer.echo(formatHappStatus(inspectHapp(platformAdapter)))
    if not platformAdapter.implemented:
        raise typer.Exit(4)


def _requireHappPlatform() -> None:
    platformAdapter = createPlan(ROOT, "happ").platform
    if not platformAdapter.implemented:
        typer.echo(platformAdapter.reason, err=True)
        raise typer.Exit(4)


@happApp.command("setup")
def happSetup(
    yes: Annotated[bool, typer.Option("--yes", help="Apply a missing verified package.")] = False,
    interactive: Annotated[
        bool, typer.Option("--interactive", help="Offer optional local link handoff after setup.")
    ] = False,
) -> None:
    """Use the existing app, or install/stage the pinned official package."""
    result = createPlan(ROOT, "happ")
    typer.echo(formatPlan(result))
    if not result.platform.implemented or result.blocked:
        raise typer.Exit(4)
    if not yes:
        typer.echo("Use --yes after reviewing this plan; no changes were made.")
        return
    status = setupHapp(ROOT, result.platform)
    typer.echo(status)
    typer.echo(formatHappStatus(inspectHapp(result.platform)))
    if interactive:
        if clipboardCommand() is None:
            typer.echo(
                "needsManualAction: clipboard handoff unavailable; use Happ's + import UI directly."
            )
        else:
            _handoffLinks(routingProfile=False, repeat=True)
            _handoffLinks(routingProfile=True, repeat=False)


def _handoffLinks(*, routingProfile: bool, repeat: bool) -> None:
    if clipboardCommand() is None:
        typer.echo("needsManualAction: no supported clipboard; enter the link directly in Happ.")
        return
    if not sys.stdin.isatty():
        typer.echo("needsManualAction: private link input requires a local terminal.")
        return
    while True:
        label = "routing profile" if routingProfile else "subscription or configuration"
        value = getpass.getpass(f"Optional {label} link (empty skips): ")
        if not value:
            typer.echo("skipped: no link entered")
            return
        try:
            kind = classifyLink(value, routingProfile=routingProfile)
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            continue
        if kind == "routingActivate":
            typer.echo(
                "needsManualAction: onadd activates or replaces a profile; review it inside Happ."
            )
            return
        try:
            copyLinkToClipboard(value)
            typer.echo(f"Clipboard handoff: {kind}. In Happ use + and import from clipboard.")
            typer.echo(
                "Review name collisions and changed app settings; "
                "clipboard/history may retain the link."
            )
            input("After Happ reports the result, press Enter to clear this clipboard handoff. ")
        finally:
            clearLinkClipboard()
            value = ""
        typer.echo(
            "needsManualCheck: verify the object inside Happ; no import result was inferred."
        )
        if not repeat:
            return


@happApp.command("addSubscription")
def happAddSubscription() -> None:
    """Offer optional local terminal-to-Happ clipboard handoff, without storing a URL."""
    _requireHappPlatform()
    _handoffLinks(routingProfile=False, repeat=True)


@happApp.command("configureRouting")
def happConfigureRouting() -> None:
    """Offer a separate routing-profile handoff; never activate a connection here."""
    _requireHappPlatform()
    typer.echo(formatHappStatus(inspectHapp()))
    _handoffLinks(routingProfile=True, repeat=False)


@happApp.command("configureAutostart")
def happConfigureAutostart() -> None:
    """Verify Happ's own setting and registration without editing its private format."""
    _requireHappPlatform()
    status = inspectHapp()
    typer.echo(formatHappStatus(status))
    if status.nativeAutostart == status.loginRegistration == "enabled":
        typer.echo("unchanged: Happ native autostart is enabled; next login needsManualCheck")
    else:
        typer.echo("needsManualAction: enable launch at login inside Happ, then rerun this command")
        raise typer.Exit(4)


@happApp.command("backup")
def happBackup() -> None:
    """Back up private Linux Happ state with SQLite's consistent backup API."""
    try:
        typer.echo(str(backupHapp()))
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"Happ backup blocked: {exc}", err=True)
        raise typer.Exit(4) from exc


@happApp.command("restore")
def happRestore(
    backupId: Annotated[str, typer.Option("--backup", help="Private Happ backup ID.")],
    applyChanges: Annotated[
        bool, typer.Option("--apply", help="Restore while Happ is closed.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm restore.")] = False,
    replaceCurrent: Annotated[
        bool,
        typer.Option(
            "--replaceCurrent", help="Replace reviewed current settings after backing them up."
        ),
    ] = False,
) -> None:
    """Preview or explicitly restore a verified Happ snapshot."""
    _requireHappPlatform()
    if applyChanges and not yes:
        raise typer.BadParameter("Restore requires --apply --yes")
    try:
        actions = restoreHapp(backupId, apply=applyChanges, replaceCurrent=replaceCurrent)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        typer.echo(f"Happ restore blocked: {exc}", err=True)
        raise typer.Exit(4) from exc
    for path, status in actions:
        typer.echo(f"{status}: {path}")
    if not applyChanges:
        typer.echo("dry-run: no files changed")


@appsApp.command("list")
def appsList() -> None:
    """Show declared sources and actual installation evidence."""
    for name, entry in catalog(ROOT).items():
        versions = ",".join(entry.supportedDebianVersions or ()) or "audit-only"
        typer.echo(
            f"{name}: {appStatus(entry)} | Debian {versions} | {entry.method} | {entry.source}"
        )


@appsApp.command("install")
def appsInstall(
    name: str,
    yes: Annotated[
        bool, typer.Option("--yes", help="Confirm the selected package action.")
    ] = False,
) -> None:
    """Install one supported Debian package after checking its current state."""
    result = createPlan(ROOT)
    if not result.platform.implemented:
        typer.echo(result.platform.reason, err=True)
        raise typer.Exit(4)
    if result.platform.distribution != "debian":
        typer.echo("UNSUPPORTED: this application adapter is Debian-only.", err=True)
        raise typer.Exit(4)
    entries = catalog(ROOT)
    if name not in entries:
        typer.echo("Unknown catalog application.", err=True)
        raise typer.Exit(2)
    entry = entries[name]
    if (
        entry.supportedDebianVersions is None
        or result.platform.version not in entry.supportedDebianVersions
    ):
        typer.echo(
            "UNSUPPORTED: no verified Debian provider for this application/version.", err=True
        )
        raise typer.Exit(4)
    status = appStatus(entry)
    if status == "unknown":
        typer.echo("blocked: package status could not be established.", err=True)
        raise typer.Exit(4)
    if name == "docker" and status.startswith("present ("):
        typer.echo("blocked: existing Docker binary has no verified package source.", err=True)
        raise typer.Exit(4)
    if name == "docker" and status.startswith("deb "):
        from setup_system.adapters.debian.docker import installDocker

        try:
            typer.echo(installDocker())
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            typer.echo(f"blocked: {exc}", err=True)
            raise typer.Exit(4) from exc
        return
    if status != "absent":
        typer.echo(f"unchanged: {name}: {status}")
        return
    typer.echo(f"{name}: source={entry.source}; method={entry.method}")
    typer.echo(f"privilege={entry.privilege}; data={entry.data}")
    if not yes:
        typer.echo("Use --yes after reviewing the plan.", err=True)
        raise typer.Exit(2)
    if name not in {"zsh", "docker"}:
        typer.echo(
            "blocked: this source requires a separate verified installation adapter.", err=True
        )
        raise typer.Exit(4)
    try:
        if name == "docker":
            from setup_system.adapters.debian.docker import installDocker

            typer.echo(installDocker())
        else:
            typer.echo(installZsh(entry))
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        typer.echo(f"failed: {exc}", err=True)
        raise typer.Exit(5) from exc


@agentsApp.command("doctor")
def agentsDoctor() -> None:
    """Inspect installed rules and profiles; no sandbox claim is inferred."""
    home = Path.home()
    codexHome = Path(os.environ.get("CODEX_HOME", home / ".codex"))
    rules = codexHome / "AGENTS.md"
    shared = home / ".agents/policy/AGENTS.md"
    typer.echo(f"Codex entry: {'present' if rules.is_file() else 'missing'}")
    typer.echo(f"Shared policy: {'present' if shared.is_file() else 'missing'}")
    matches = rules.is_file() and shared.is_file() and rules.read_bytes() == shared.read_bytes()
    typer.echo(f"Entry matches policy: {matches}")
    for name in ("review", "project", "network"):
        path = codexHome / f"{name}.config.toml"
        typer.echo(f"{name} profile: {'present' if path.is_file() else 'missing'}")
    typer.echo("Permission enforcement requires separate sentinel probes in a new scoped session.")


@agentsApp.command("launch")
def agentsLaunch(
    scope: str,
    mode: Annotated[str, typer.Option(help="review, project or network.")] = "review",
) -> None:
    """Open Codex in one allowlisted project root with a named permission profile."""
    if mode not in {"review", "project", "network"}:
        raise typer.BadParameter("Choose review, project or network")
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "agents/scopes.toml"
    data = tomllib.loads(config.read_text())
    record = data.get("scope", {}).get(scope)
    if not record:
        raise typer.BadParameter("Unknown local scope")
    root = Path(record["path"]).resolve()
    if not root.is_dir() or root == Path.home().resolve():
        raise typer.BadParameter("Scope is missing or too broad")
    git = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if git.returncode != 0 or Path(git.stdout.strip()).resolve() != root:
        raise typer.BadParameter("Scope is not an exact Git root")
    executable = shutil.which("codex")
    if not executable:
        raise typer.BadParameter("Codex is unavailable")
    raise typer.Exit(
        subprocess.run([executable, "--profile", mode, "--cd", str(root)], check=False).returncode
    )


@templatesApp.command("list")
def templatesList() -> None:
    """Show the separate generic template owner when it exists."""
    location = Path.home() / "Projects/projectTemplates"
    typer.echo(f"Generic templates: {location if location.is_dir() else 'not installed'}")


def _discoverProjects() -> dict[str, Path]:
    home = Path.home()
    result: dict[str, Path] = {}
    for rootName in ("Projects", "pokerlost"):
        root = home / rootName
        if not root.is_dir() or root.is_symlink():
            continue
        for child in root.iterdir():
            if child.is_dir() and not child.is_symlink() and (child / ".git").exists():
                result[f"{rootName}/{child.name}"] = child
    for name in ("setupSystem", "Projects/projectTemplates"):
        candidate = home / name
        if candidate.is_dir() and not candidate.is_symlink():
            result[name] = candidate
    return dict(sorted(result.items()))


@projectsApp.command("list")
def projectsList() -> None:
    """Discover bounded local roots without scanning the home or vault."""
    for name, path in _discoverProjects().items():
        typer.echo(f"{name}: {path}")


@projectsApp.command("open")
def projectsOpen(
    name: Annotated[str | None, typer.Option(help="Name from projects list.")] = None,
    tmux: Annotated[bool, typer.Option(help="Open in a named tmux session.")] = False,
) -> None:
    """Launch Neovim from one selected project root."""
    projects = _discoverProjects()
    if name is None:
        for key in projects:
            typer.echo(key)
        try:
            name = typer.prompt("Project (empty cancels)", default="", show_default=False)
        except (EOFError, KeyboardInterrupt):
            raise typer.Exit(2) from None
        if not name:
            typer.echo("cancelled")
            return
    projectRoot = projects.get(name)
    if projectRoot is None:
        raise typer.BadParameter("Choose an exact name from projects list")
    editor = Path.home() / ".local/bin/nvim"
    if not editor.is_file() and not editor.is_symlink():
        raise typer.BadParameter("Managed Neovim is missing; run apply --profile terminalDev")
    if tmux:
        command = shutil.which("tmux")
        if command is None:
            raise typer.BadParameter("tmux is unavailable")
        sessionName = "dev-" + "".join(character for character in name if character.isalnum())[:30]
        exists = subprocess.run([command, "has-session", "-t", sessionName], timeout=5, check=False)
        if exists.returncode:
            subprocess.run(
                [
                    command,
                    "new-session",
                    "-d",
                    "-s",
                    sessionName,
                    "-c",
                    str(projectRoot),
                    str(editor),
                ],
                timeout=10,
                check=True,
            )
            subprocess.run(
                [command, "new-window", "-t", sessionName, "-c", str(projectRoot)],
                timeout=10,
                check=True,
            )
        raise typer.Exit(
            subprocess.run([command, "attach-session", "-t", sessionName], check=False).returncode
        )
    raise typer.Exit(subprocess.run([str(editor)], cwd=projectRoot, check=False).returncode)
