"""Adapt application interaction to Typer's existing terminal presentation."""

from __future__ import annotations

import sys

import typer

from setup_system.application.interaction import ConsoleInteraction


class TyperInteraction(ConsoleInteraction):
    def write(
        self, text: str, *, end: str = "\n", error: bool = False, flush: bool = False
    ) -> None:
        typer.echo(text + end, nl=False, err=error)
        if flush:
            (sys.stderr if error else sys.stdout).flush()

    def confirm(self, prompt: str) -> bool:
        return typer.confirm(prompt, default=False)
