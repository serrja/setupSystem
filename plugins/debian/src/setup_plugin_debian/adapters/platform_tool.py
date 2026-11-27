"""Call only installed platform probes and reviewed resource checks."""

import json
import os
from pathlib import Path
from typing import Literal

import typer
from pydantic import JsonValue, TypeAdapter

from setup_plugin_debian.adapters import apt, inspection, layout, native, platform

cli = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


def _emit(result: object) -> None:
    print(json.dumps(result))


@cli.command("platform")
def platform_command() -> None:
    """Read the current supported platform identity."""
    _emit(platform.current_target())


@cli.command("boot")
def boot_command() -> None:
    """Read the current boot identity."""
    _emit(platform.current_boot_identity())


@cli.command("installed")
def installed_command(name: str) -> None:
    """Read the installed state of one validated package."""
    if os.geteuid() != 0:
        raise ValueError("Root authorization is required for installed package facts")
    _emit(apt.installed(TypeAdapter(apt.PackageName).validate_python(name)))


@cli.command("inspect")
def inspect_command(home: Path) -> None:
    """Inspect the selected user's platform state."""
    _emit(inspection.inspect_system(home).model_dump(mode="json"))


@cli.command("observations")
def observations_command(home: Path) -> None:
    """Read bounded platform observations."""
    _emit(inspection.observations(home))


@cli.command("verify-resource")
def verify_resource_command(path: Path, relative: str) -> None:
    """Check that a package-owned resource still matches its declaration."""
    layout.require_installed_resource(path, relative)
    _emit(True)


@cli.command("play")
def play_command(plugin: str, recipe: str, method: str, descriptor: int) -> None:
    """Apply one approved native recipe after reading a bounded descriptor."""
    auth = TypeAdapter(Literal["su", "sudo"]).validate_python(method)
    with os.fdopen(descriptor, "rb") as stream:
        payload = stream.read(65537)
    if len(payload) > 65536:
        raise ValueError("Oversized native request")
    native.run_native_play(
        plugin, recipe, TypeAdapter(dict[str, JsonValue]).validate_json(payload), auth
    )
    _emit(True)


def main() -> None:
    try:
        cli()
    except Exception:
        typer.echo(
            "Error [native_executor_unavailable]: Platform request did not complete.",
            err=True,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
