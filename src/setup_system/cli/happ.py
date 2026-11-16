"""Happ desktop package and private-state operations."""

from __future__ import annotations

import getpass
import json
import sys
from typing import Annotated

import typer

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
from setup_system.cli._context import ROOT
from setup_system.planning import create_plan, format_plan

happ_app = typer.Typer(help="Happ desktop package and private-state operations.")


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
