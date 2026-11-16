"""Debian workstation setup and recovery."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from setup_system.application.apps import app_status, catalog, install_zsh
from setup_system.application.configuration import apply_configuration
from setup_system.application.happ import (
    backup_happ,
    format_happ_status,
    inspect_happ,
    setup_happ,
)
from setup_system.application.terminal_dev import (
    install_archive_tool,
    install_language_tools,
    install_neovim,
    install_parsers,
    install_plugins,
    install_system_tools,
)
from setup_system.cli._context import ROOT
from setup_system.cli.shell import _apply_default_shell
from setup_system.planning import create_plan, format_plan
from setup_system.storage import new_backup
from setup_system.storage import restore as restore_backup

app = typer.Typer(
    help="Debian workstation setup and recovery.",
    no_args_is_help=False,
    invoke_without_command=True,
)


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
    if profile == "terminalDev":
        try:
            typer.echo(install_zsh(catalog(ROOT)["zsh"]))
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            typer.echo(f"Zsh installation blocked: {exc}", err=True)
            raise typer.Exit(4) from exc
    try:
        backup, statuses = apply_configuration(ROOT, terminal_dev=profile == "terminalDev")
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        typer.echo(f"failed: {exc}", err=True)
        raise typer.Exit(5) from exc
    typer.echo(f"backup: {backup}")
    for path, status in statuses:
        typer.echo(f"{status}: {path}")
    if profile == "terminalDev":
        _apply_default_shell()
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
