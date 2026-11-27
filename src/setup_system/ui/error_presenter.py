"""Present the shared safe error contract in CLI and Textual surfaces."""

import os
import sys
from typing import Any

import typer
from platformdirs import user_state_path
from setup_system_sdk.schemas.errors import Error
from textual import events
from textual.widgets import Static


def error_line(detail: Error) -> str:
    return f"Error [{detail.code.value}]: {detail.message}"


def show_cli(detail: Error) -> None:
    typer.secho(
        error_line(detail),
        fg=typer.colors.RED,
        err=True,
        color=sys.stderr.isatty() and "NO_COLOR" not in os.environ,
    )
    if detail.hint:
        typer.echo(f"Next: {detail.hint}", err=True)


class ErrorPanel(Static):
    """Keep one action's error visible until the next action replaces it."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._detail: Error | None = None
        self._expanded = False

    def set_error(self, detail: Error) -> None:
        self._detail = detail
        self._expanded = False
        self._render_error()

    def _render_error(self) -> None:
        assert self._detail is not None
        detail = self._detail
        text = error_line(detail)
        if detail.diagnostic_id:
            text += " · Details (click)"
            if self._expanded:
                path = user_state_path("setup-system") / "diagnostics/failures.jsonl"
                text += f"\nDiagnostic ID: {detail.diagnostic_id}\nLog: {path}"
        super().update(text)

    def on_click(self, event: events.Click) -> None:
        if self._detail and self._detail.diagnostic_id:
            self._expanded = not self._expanded
            self._render_error()
            event.stop()

    def update(self, content: Any = "", *, layout: bool = True) -> None:
        self._detail = None
        self._expanded = False
        super().update(content, layout=layout)

    def clear_error(self) -> None:
        self.update("")
