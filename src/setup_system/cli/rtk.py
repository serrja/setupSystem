"""Pinned Rust Token Killer developer tool."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from setup_system.application.rtk import inspect_rtk, install_rtk, plan_rtk, restore_rtk
from setup_system.cli._context import ROOT

rtk_app = typer.Typer(help="Pinned Rust Token Killer developer tool.")


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
