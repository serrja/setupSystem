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

from setup_system.application.apps import app_status, catalog, install_zsh
from setup_system.application.configuration import apply_configuration
from setup_system.application.happ import (
    backup_happ,
    classify_link,
    clear_link_clipboard,
    clipboard_command,
    copy_link_to_clipboard,
    format_happ_status,
    inspect_happ,
    restore_happ,
    setup_happ,
)
from setup_system.application.project_layout import (
    active_projects,
    apply_migration,
    format_migration_plan,
    load_layout,
    plan_migration,
    prepare_owner_directories,
)
from setup_system.application.rtk import inspect_rtk, install_rtk, plan_rtk, restore_rtk
from setup_system.application.shell import (
    apply_shell,
    change_login_shell,
    plan_shell,
    restore_shell,
)
from setup_system.application.terminal_dev import (
    install_archive_tool,
    install_language_tools,
    install_neovim,
    install_parsers,
    install_plugins,
    install_system_tools,
)
from setup_system.planning import create_plan, format_plan
from setup_system.storage import new_backup
from setup_system.storage import restore as restore_backup

SOURCE_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ROOT = SOURCE_ROOT if (SOURCE_ROOT / "manifests/platforms.toml").is_file() else PACKAGE_ROOT
app = typer.Typer(help="Debian workstation setup and recovery.", no_args_is_help=True)
apps_app = typer.Typer(help="Applications and installation sources.")
agents_app = typer.Typer(help="Scoped agent configuration.")
templates_app = typer.Typer(help="Generic project templates.")
projects_app = typer.Typer(help="Open one discovered project root.")
happ_app = typer.Typer(help="Happ desktop package and private-state operations.")
shell_app = typer.Typer(help="Portable Zsh setup for the current user.")
rtk_app = typer.Typer(help="Pinned Rust Token Killer developer tool.")
app.add_typer(apps_app, name="apps")
app.add_typer(agents_app, name="agents")
app.add_typer(templates_app, name="templates")
app.add_typer(projects_app, name="projects")
app.add_typer(happ_app, name="happ")
app.add_typer(shell_app, name="shell")
app.add_typer(rtk_app, name="rtk")


@rtk_app.command("plan")
def rtk_plan() -> None:
    """Report the provider action without contacting the network."""
    try:
        status, action = plan_rtk(ROOT)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        typer.echo(f"blocked: {exc}", err=True)
        raise typer.Exit(4) from exc
    typer.echo(f"product: rtk-ai/rtk; path: {status.path or 'absent'}")
    typer.echo(f"installed version: {status.version or 'unknown'}")
    typer.echo(action)
    if action.startswith("blocked:"):
        raise typer.Exit(4)


@rtk_app.command("doctor")
def rtk_doctor() -> None:
    """Check the binary and local Codex hook without reading command history."""
    rtk_plan()
    status = inspect_rtk()
    hook = Path.home() / ".codex/hooks.json"
    configured = False
    if hook.is_file():
        data = json.loads(hook.read_text())
        configured = any(
            item.get("command") == "rtk hook codex"
            for group in data.get("hooks", {}).get("PreToolUse", [])
            for item in group.get("hooks", [])
        )
    typer.echo(f"Codex hook configured: {configured}")
    if status.path is not None:
        typer.echo(f"binary owned by current user: {status.path.stat().st_uid == os.getuid()}")


@rtk_app.command("apply")
def rtk_apply(
    yes: Annotated[bool, typer.Option("--yes", help="Install the pinned RTK release.")] = False,
    update: Annotated[
        bool, typer.Option("--update", help="Replace a different RTK version.")
    ] = False,
) -> None:
    """Preserve a suitable binary or install one checked release asset."""
    if not yes:
        rtk_plan()
        typer.echo("Use --yes after reviewing the plan.", err=True)
        raise typer.Exit(2)
    try:
        result, backup = install_rtk(ROOT, update=update)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        typer.echo(f"failed: {exc}", err=True)
        raise typer.Exit(5) from exc
    typer.echo(result)
    if backup is not None:
        typer.echo(f"backup: {backup}")


@rtk_app.command("restore")
def rtk_restore(
    backup_id: Annotated[str, typer.Option("--backup", help="RTK install backup ID.")],
    yes: Annotated[bool, typer.Option("--yes", help="Restore the prior binary.")] = False,
) -> None:
    """Restore the previous RTK binary after checking for later edits."""
    if not yes:
        raise typer.BadParameter("Use --yes after reviewing the backup ID")
    try:
        typer.echo(restore_rtk(backup_id))
    except (OSError, ValueError, KeyError) as exc:
        typer.echo(f"blocked: {exc}", err=True)
        raise typer.Exit(4) from exc


@shell_app.command("plan")
def shell_plan() -> None:
    """Inspect Zsh startup changes without modifying the host."""
    try:
        status, changes = plan_shell(ROOT)
    except (OSError, ValueError) as exc:
        typer.echo(f"blocked: {exc}", err=True)
        raise typer.Exit(4) from exc
    typer.echo(f"account: {status.user}; login shell: {status.login_shell}")
    typer.echo(f"Zsh: {status.zsh or 'absent'}; registered: {status.registered}")
    typer.echo(f"startup directory: {status.startup_dir}")
    for path, result in changes:
        typer.echo(f"{result}: {path}")


@shell_app.command("apply")
def shell_apply(
    yes: Annotated[bool, typer.Option("--yes", help="Apply the reviewed Zsh plan.")] = False,
) -> None:
    """Back up and apply Zsh files, then use chsh when registered."""
    if not yes:
        shell_plan()
        typer.echo("Use --yes after reviewing the plan.", err=True)
        raise typer.Exit(2)
    try:
        backup, results, status = apply_shell(ROOT)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        typer.echo(f"failed: {exc}", err=True)
        raise typer.Exit(5) from exc
    typer.echo(f"backup: {backup}")
    for path, result in results:
        typer.echo(f"{result}: {path}")
    if status.zsh is None:
        raise typer.Exit(4)
    if status.login_shell != status.zsh:
        try:
            changed, detail = change_login_shell(status.zsh)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            typer.echo(f"login shell blocked: {exc}", err=True)
            raise typer.Exit(4) from exc
        typer.echo(f"login shell: {detail}")
        if not changed:
            raise typer.Exit(4)


@shell_app.command("doctor")
def shell_doctor() -> None:
    """Read back account and startup state without side effects."""
    shell_plan()


@shell_app.command("restore")
def shell_restore(
    backup_id: Annotated[str, typer.Option("--backup", help="Shell apply backup ID.")],
    yes: Annotated[bool, typer.Option("--yes", help="Restore unchanged managed files.")] = False,
) -> None:
    """Restore selected Zsh files while protecting edits made after apply."""
    if not yes:
        typer.echo("Use --yes after reviewing the backup ID.", err=True)
        raise typer.Exit(2)
    try:
        for path, result in restore_shell(backup_id):
            typer.echo(f"{result}: {path}")
    except (OSError, ValueError, KeyError) as exc:
        typer.echo(f"blocked: {exc}", err=True)
        raise typer.Exit(4) from exc
    typer.echo("Login shell is restored separately with chsh to the saved previous path.")


@app.command()
def plan(profile: Annotated[str, typer.Option(help="base, terminalDev or happ.")] = "base") -> None:
    """Show a read-only plan, including unsupported-platform status."""
    result = create_plan(ROOT, profile)
    typer.echo(format_plan(result))
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
    result = create_plan(ROOT, profile)
    if not result.platform.implemented or result.blocked:
        typer.echo(format_plan(result), err=True)
        raise typer.Exit(4)
    if not yes:
        typer.echo(format_plan(result))
        if not typer.confirm("Apply user configuration?", default=False):
            raise typer.Exit(2)
    try:
        backup, statuses = apply_configuration(ROOT, terminal_dev=profile == "terminalDev")
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        typer.echo(f"failed: {exc}", err=True)
        raise typer.Exit(5) from exc
    typer.echo(f"backup: {backup}")
    for path, status in statuses:
        typer.echo(f"{status}: {path}")
    if profile == "terminalDev":
        try:
            typer.echo(install_neovim(ROOT, result.platform))
            for status in install_plugins(ROOT):
                typer.echo(status)
            for tool_name in ("stylua", "tree-sitter"):
                typer.echo(install_archive_tool(ROOT, result.platform, tool_name))
            for status in install_language_tools(ROOT):
                typer.echo(status)
            typer.echo(install_parsers(ROOT))
            tool_status, blocked = install_system_tools(ROOT, result.platform)
            typer.echo(tool_status)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            typer.echo(f"terminalDev failed: {exc}", err=True)
            raise typer.Exit(5) from exc
        if blocked:
            raise typer.Exit(4)
    if profile == "happ":
        try:
            typer.echo(setup_happ(ROOT, result.platform))
            typer.echo(format_happ_status(inspect_happ(result.platform)))
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            typer.echo(f"Happ setup failed: {exc}", err=True)
            raise typer.Exit(5) from exc


@app.command()
def doctor(
    profile: Annotated[str, typer.Option(help="base, terminalDev or happ.")] = "base",
) -> None:
    """Inspect the active host without changing it."""
    result = create_plan(ROOT, profile)
    typer.echo(format_plan(result).splitlines()[0])
    for name, entry in catalog(ROOT).items():
        typer.echo(f"{name}: {app_status(entry)}")
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
        typer.echo(format_happ_status(inspect_happ(result.platform)))
    if not result.platform.implemented or result.blocked:
        raise typer.Exit(4)


@app.command()
def backup(
    include_vault: Annotated[
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
            typer.echo(str(backup_happ()))
        except (OSError, ValueError, RuntimeError) as exc:
            typer.echo(f"Happ backup blocked: {exc}", err=True)
            raise typer.Exit(4) from exc
        return
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex"))
    files = [
        home / ".zshrc",
        home / ".bashrc",
        home / ".gitconfig",
        codex_home / "config.toml",
        codex_home / "AGENTS.md",
    ]
    files += list((config / "zsh").glob("*.zsh")) + list((config / "git").glob("*.inc"))
    files += list((home / ".agents").rglob("*.md"))
    if profile == "terminalDev":
        files += [item for item in (config / "nvim").rglob("*") if item.is_file()]
        files += [item for item in (config / "tmux").rglob("*") if item.is_file()]
    if include_vault:
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
        folder = new_backup(files)
    except (OSError, ValueError) as exc:
        typer.echo(f"backup failed: {exc}", err=True)
        raise typer.Exit(5) from exc
    typer.echo(str(folder))


@app.command()
def restore(
    backup_id: Annotated[
        str, typer.Option("--backup", help="Backup ID from the private backup directory.")
    ],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show changes without writing (default).")
    ] = True,
    apply_changes: Annotated[bool, typer.Option("--apply", help="Restore verified files.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm non-interactive restore.")] = False,
    replace_current: Annotated[
        bool,
        typer.Option("--replaceCurrent", help="Back up and replace conflicting current files."),
    ] = False,
) -> None:
    """Verify a backup and optionally restore its files."""
    if apply_changes:
        platform_adapter = create_plan(ROOT).platform
        if not platform_adapter.implemented:
            typer.echo(platform_adapter.reason, err=True)
            raise typer.Exit(4)
    if apply_changes and not yes:
        typer.echo("Restore requires --apply --yes.", err=True)
        raise typer.Exit(2)
    if replace_current and not apply_changes:
        typer.echo("--replaceCurrent requires --apply --yes.", err=True)
        raise typer.Exit(2)
    try:
        statuses = restore_backup(backup_id, apply=apply_changes, replace_current=replace_current)
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"restore failed: {exc}", err=True)
        raise typer.Exit(5) from exc
    for path, status in statuses:
        typer.echo(f"{status}: {path}")
    if apply_changes and any(status == "conflict" for _, status in statuses):
        typer.echo(
            "Conflicts preserved; use --replaceCurrent only after reviewing dry-run.", err=True
        )
        raise typer.Exit(4)
    if dry_run and not apply_changes:
        typer.echo("dry-run: no files changed")


@happ_app.command("doctor")
def happ_doctor() -> None:
    """Read safe package, native-autostart and routing categories only."""
    platform_adapter = create_plan(ROOT, "happ").platform
    typer.echo(format_happ_status(inspect_happ(platform_adapter)))
    if not platform_adapter.implemented:
        raise typer.Exit(4)


def _require_happ_platform() -> None:
    platform_adapter = create_plan(ROOT, "happ").platform
    if not platform_adapter.implemented:
        typer.echo(platform_adapter.reason, err=True)
        raise typer.Exit(4)


@happ_app.command("setup")
def happ_setup(
    yes: Annotated[bool, typer.Option("--yes", help="Apply a missing verified package.")] = False,
    interactive: Annotated[
        bool, typer.Option("--interactive", help="Offer optional local link handoff after setup.")
    ] = False,
) -> None:
    """Use the existing app, or install/stage the pinned official package."""
    result = create_plan(ROOT, "happ")
    typer.echo(format_plan(result))
    if not result.platform.implemented or result.blocked:
        raise typer.Exit(4)
    if not yes:
        typer.echo("Use --yes after reviewing this plan; no changes were made.")
        return
    status = setup_happ(ROOT, result.platform)
    typer.echo(status)
    typer.echo(format_happ_status(inspect_happ(result.platform)))
    if interactive:
        if clipboard_command() is None:
            typer.echo(
                "needsManualAction: clipboard handoff unavailable; use Happ's + import UI directly."
            )
        else:
            _handoff_links(routing_profile=False, repeat=True)
            _handoff_links(routing_profile=True, repeat=False)


def _handoff_links(*, routing_profile: bool, repeat: bool) -> None:
    if clipboard_command() is None:
        typer.echo("needsManualAction: no supported clipboard; enter the link directly in Happ.")
        return
    if not sys.stdin.isatty():
        typer.echo("needsManualAction: private link input requires a local terminal.")
        return
    while True:
        label = "routing profile" if routing_profile else "subscription or configuration"
        value = getpass.getpass(f"Optional {label} link (empty skips): ")
        if not value:
            typer.echo("skipped: no link entered")
            return
        try:
            kind = classify_link(value, routing_profile=routing_profile)
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            continue
        if kind == "routingActivate":
            typer.echo(
                "needsManualAction: onadd activates or replaces a profile; review it inside Happ."
            )
            return
        try:
            copy_link_to_clipboard(value)
            typer.echo(f"Clipboard handoff: {kind}. In Happ use + and import from clipboard.")
            typer.echo(
                "Review name collisions and changed app settings; "
                "clipboard/history may retain the link."
            )
            input("After Happ reports the result, press Enter to clear this clipboard handoff. ")
        finally:
            clear_link_clipboard()
            value = ""
        typer.echo(
            "needsManualCheck: verify the object inside Happ; no import result was inferred."
        )
        if not repeat:
            return


@happ_app.command("addSubscription")
def happ_add_subscription() -> None:
    """Offer optional local terminal-to-Happ clipboard handoff, without storing a URL."""
    _require_happ_platform()
    _handoff_links(routing_profile=False, repeat=True)


@happ_app.command("configureRouting")
def happ_configure_routing() -> None:
    """Offer a separate routing-profile handoff; never activate a connection here."""
    _require_happ_platform()
    typer.echo(format_happ_status(inspect_happ()))
    _handoff_links(routing_profile=True, repeat=False)


@happ_app.command("configureAutostart")
def happ_configure_autostart() -> None:
    """Verify Happ's own setting and registration without editing its private format."""
    _require_happ_platform()
    status = inspect_happ()
    typer.echo(format_happ_status(status))
    if status.native_autostart == status.login_registration == "enabled":
        typer.echo("unchanged: Happ native autostart is enabled; next login needsManualCheck")
    else:
        typer.echo("needsManualAction: enable launch at login inside Happ, then rerun this command")
        raise typer.Exit(4)


@happ_app.command("backup")
def happ_backup() -> None:
    """Back up private Linux Happ state with SQLite's consistent backup API."""
    try:
        typer.echo(str(backup_happ()))
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"Happ backup blocked: {exc}", err=True)
        raise typer.Exit(4) from exc


@happ_app.command("restore")
def happ_restore(
    backup_id: Annotated[str, typer.Option("--backup", help="Private Happ backup ID.")],
    apply_changes: Annotated[
        bool, typer.Option("--apply", help="Restore while Happ is closed.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm restore.")] = False,
    replace_current: Annotated[
        bool,
        typer.Option(
            "--replaceCurrent", help="Replace reviewed current settings after backing them up."
        ),
    ] = False,
) -> None:
    """Preview or explicitly restore a verified Happ snapshot."""
    _require_happ_platform()
    if apply_changes and not yes:
        raise typer.BadParameter("Restore requires --apply --yes")
    try:
        actions = restore_happ(backup_id, apply=apply_changes, replace_current=replace_current)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        typer.echo(f"Happ restore blocked: {exc}", err=True)
        raise typer.Exit(4) from exc
    for path, status in actions:
        typer.echo(f"{status}: {path}")
    if not apply_changes:
        typer.echo("dry-run: no files changed")


@apps_app.command("list")
def apps_list() -> None:
    """Show declared sources and actual installation evidence."""
    for name, entry in catalog(ROOT).items():
        versions = ",".join(entry.supported_debian_versions or ()) or "audit-only"
        typer.echo(
            f"{name}: {app_status(entry)} | Debian {versions} | {entry.method} | {entry.source}"
        )


@apps_app.command("install")
def apps_install(
    name: str,
    yes: Annotated[
        bool, typer.Option("--yes", help="Confirm the selected package action.")
    ] = False,
) -> None:
    """Install one supported Debian package after checking its current state."""
    result = create_plan(ROOT)
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
        entry.supported_debian_versions is None
        or result.platform.version not in entry.supported_debian_versions
    ):
        typer.echo(
            "UNSUPPORTED: no verified Debian provider for this application/version.", err=True
        )
        raise typer.Exit(4)
    status = app_status(entry)
    if status == "unknown":
        typer.echo("blocked: package status could not be established.", err=True)
        raise typer.Exit(4)
    if name == "docker" and status.startswith("present ("):
        typer.echo("blocked: existing Docker binary has no verified package source.", err=True)
        raise typer.Exit(4)
    if name == "docker" and status.startswith("deb "):
        from setup_system.adapters.debian.docker import install_docker

        try:
            typer.echo(install_docker())
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
            from setup_system.adapters.debian.docker import install_docker

            typer.echo(install_docker())
        else:
            typer.echo(install_zsh(entry))
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        typer.echo(f"failed: {exc}", err=True)
        raise typer.Exit(5) from exc


@agents_app.command("doctor")
def agents_doctor() -> None:
    """Inspect installed rules and profiles; no sandbox claim is inferred."""
    home = Path.home()
    codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex"))
    rules = codex_home / "AGENTS.md"
    shared = home / ".agents/policy/AGENTS.md"
    typer.echo(f"Codex entry: {'present' if rules.is_file() else 'missing'}")
    typer.echo(f"Shared policy: {'present' if shared.is_file() else 'missing'}")
    matches = rules.is_file() and shared.is_file() and rules.read_bytes() == shared.read_bytes()
    typer.echo(f"Entry matches policy: {matches}")
    for name in ("review", "project", "network"):
        path = codex_home / f"{name}.config.toml"
        typer.echo(f"{name} profile: {'present' if path.is_file() else 'missing'}")
    typer.echo("Permission enforcement requires separate sentinel probes in a new scoped session.")


@agents_app.command("launch")
def agents_launch(
    scope: str,
    mode: Annotated[str, typer.Option(help="review, project or network.")] = "review",
) -> None:
    """Open Codex in one allowlisted project root with a named permission profile."""
    if mode not in {"review", "project", "network"}:
        raise typer.BadParameter("Choose review, project or network")
    layout = load_layout(ROOT)
    try:
        projects = active_projects(layout)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if scope in {project.id for project in layout.projects}:
        root = projects.get(scope)
        if root is None:
            raise typer.BadParameter("Catalog project is missing or has no exact Git root")
    else:
        if scope in {project.id.split("/", 1)[1] for project in layout.projects}:
            raise typer.BadParameter("Use the owner/project ID from projects list")
        config = (
            Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "agents/scopes.toml"
        )
        data = tomllib.loads(config.read_text()) if config.is_file() else {}
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


@templates_app.command("list")
def templates_list() -> None:
    """Show the two declared generic template sources and their local state."""
    layout = load_layout(ROOT)
    projects = active_projects(layout)
    for name in ("serrja/pythonTemplate", "serrja/fastApiTemplate"):
        typer.echo(f"{name}: {projects.get(name, 'not installed')}")


@templates_app.command("path")
def templates_path(
    name: Annotated[str, typer.Option(help="pythonTemplate or fastApiTemplate.")],
) -> None:
    """Resolve an existing template checkout from the shared project catalog."""
    if name not in {"pythonTemplate", "fastApiTemplate"}:
        raise typer.BadParameter("Choose pythonTemplate or fastApiTemplate")
    source = active_projects(load_layout(ROOT)).get(f"serrja/{name}")
    if source is None:
        typer.echo("template source is not installed", err=True)
        raise typer.Exit(4)
    typer.echo(str(source))


def _discover_projects() -> dict[str, Path]:
    return dict(sorted(active_projects(load_layout(ROOT)).items()))


@projects_app.command("list")
def projects_list() -> None:
    """List exact owned Git roots from the canonical project catalog."""
    try:
        projects = _discover_projects()
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    for name, path in projects.items():
        typer.echo(f"{name}: {path}")


@projects_app.command("planMigration")
def projects_plan_migration() -> None:
    """Inspect old and target paths without creating directories or touching Git."""
    layout = load_layout(ROOT)
    typer.echo(format_migration_plan(layout, plan_migration(layout)))


@projects_app.command("move")
def projects_move(
    name: Annotated[str, typer.Option(help="Exact owner/project ID from projects list.")],
    backup_manifest: Annotated[
        Path,
        typer.Option(
            "--backup-manifest", help="Private manifest for the current full-tree Git backup."
        ),
    ],
    yes: Annotated[bool, typer.Option("--yes", help="Apply one checked relocation.")] = False,
    accept_owner_review: Annotated[
        bool, typer.Option("--accept-owner-review", help="Keep a reviewed foreign-owner origin.")
    ] = False,
) -> None:
    """Move one ordinary checkout on the same filesystem after backup verification."""
    layout = load_layout(ROOT)
    moves = {move.project.id: move for move in plan_migration(layout)}
    move = moves.get(name)
    if move is None:
        raise typer.BadParameter("Choose one exact catalog project ID")
    typer.echo(f"{name}: {move.source or 'absent'} -> {move.project.target} [{move.status}]")
    if not yes:
        raise typer.Exit(2)
    state_root = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    try:
        result_path = apply_migration(
            layout,
            name,
            backup_manifest=backup_manifest,
            result_directory=state_root / "setup-system/moves",
            accept_owner_review=accept_owner_review,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"relocation blocked: {exc}", err=True)
        raise typer.Exit(4) from exc
    typer.echo(f"relocation verified: {result_path}")


@projects_app.command("prepare")
def projects_prepare(
    yes: Annotated[
        bool, typer.Option("--yes", help="Create only owner directories on Debian.")
    ] = False,
) -> None:
    """Explicitly prepare the projects root and owner groups on supported Debian."""
    from setup_system.planning import detect_platform

    platform = detect_platform()
    if not platform.implemented:
        typer.echo(platform.reason, err=True)
        raise typer.Exit(4)
    layout = load_layout(ROOT)
    if not yes:
        for path in (
            layout.projects_root,
            *(layout.projects_root / group for group in layout.owner_groups),
        ):
            typer.echo(f"would create if absent: {path}")
        raise typer.Exit(2)
    try:
        for path in prepare_owner_directories(layout):
            typer.echo(f"present: {path}")
    except (OSError, ValueError) as exc:
        typer.echo(f"layout blocked: {exc}", err=True)
        raise typer.Exit(4) from exc


def _choose_project(projects: dict[str, Path], name: str | None) -> Path | None:
    if name is None:
        if not sys.stdin.isatty():
            raise typer.BadParameter("Pass --name when stdin is not interactive")
        selector = shutil.which("fzf")
        if selector is not None:
            selected = subprocess.run(
                [selector, "--prompt", "Project> "],
                input="\n".join(projects) + "\n",
                stdout=subprocess.PIPE,
                text=True,
                timeout=120,
                check=False,
            )
            name = selected.stdout.strip() if selected.returncode == 0 else ""
        else:
            for key in projects:
                typer.echo(key, err=True)
            sys.stderr.write("Project (empty cancels): ")
            sys.stderr.flush()
            name = sys.stdin.readline().strip()
        if not name:
            return None
    root = projects.get(name)
    if root is None:
        raise typer.BadParameter("Choose an exact name from projects list")
    return root


@projects_app.command("path")
def projects_path(
    name: Annotated[str | None, typer.Option(help="Exact name from projects list.")] = None,
) -> None:
    """Print one selected path for a shell function without changing the shell cwd."""
    try:
        project_root = _choose_project(_discover_projects(), name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if project_root is not None:
        typer.echo(str(project_root))


@projects_app.command("open")
def projects_open(
    name: Annotated[str | None, typer.Option(help="Name from projects list.")] = None,
    tmux: Annotated[bool, typer.Option(help="Open in a named tmux session.")] = False,
) -> None:
    """Launch Neovim from one selected project root."""
    try:
        project_root = _choose_project(_discover_projects(), name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if project_root is None:
        typer.echo("cancelled")
        return
    editor = Path.home() / ".local/bin/nvim"
    if not editor.is_file() and not editor.is_symlink():
        raise typer.BadParameter("Managed Neovim is missing; run apply --profile terminalDev")
    if tmux:
        command = shutil.which("tmux")
        if command is None:
            raise typer.BadParameter("tmux is unavailable")
        label = f"{project_root.parent.name}-{project_root.name}"
        session_name = (
            "dev-" + "".join(character for character in label if character.isalnum())[:30]
        )
        exists = subprocess.run(
            [command, "has-session", "-t", session_name], timeout=5, check=False
        )
        if exists.returncode:
            subprocess.run(
                [
                    command,
                    "new-session",
                    "-d",
                    "-s",
                    session_name,
                    "-c",
                    str(project_root),
                    str(editor),
                ],
                timeout=10,
                check=True,
            )
            subprocess.run(
                [command, "new-window", "-t", session_name, "-c", str(project_root)],
                timeout=10,
                check=True,
            )
        raise typer.Exit(
            subprocess.run([command, "attach-session", "-t", session_name], check=False).returncode
        )
    raise typer.Exit(subprocess.run([str(editor)], cwd=project_root, check=False).returncode)
