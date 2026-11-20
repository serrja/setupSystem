"""Interaction port shared by console automation and the terminal application."""

from __future__ import annotations

import sys
from typing import Protocol


class Interaction(Protocol):
    @property
    def cancelled(self) -> bool: ...
    @property
    def interactive(self) -> bool: ...
    def write(
        self, text: str, *, end: str = "\n", error: bool = False, flush: bool = False
    ) -> None: ...
    def confirm(self, prompt: str) -> bool: ...


class ConsoleInteraction:
    @property
    def cancelled(self) -> bool:
        return False

    @property
    def interactive(self) -> bool:
        return sys.stdin.isatty()

    def write(
        self, text: str, *, end: str = "\n", error: bool = False, flush: bool = False
    ) -> None:
        print(text, end=end, file=sys.stderr if error else sys.stdout, flush=flush)

    def confirm(self, prompt: str) -> bool:
        return input(prompt + " [y/N] ").strip().lower() in {"y", "yes"}
