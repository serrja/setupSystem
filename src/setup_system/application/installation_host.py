"""Typed local process boundary between Textual and the synchronous installer."""

from __future__ import annotations

import os
import signal
from dataclasses import dataclass
from multiprocessing.connection import Connection
from pathlib import Path
from types import FrameType

from setup_system.application.installer import InstallationService, InstallRequest
from setup_system.diagnostics import error_text


@dataclass(frozen=True)
class Progress:
    text: str


@dataclass(frozen=True)
class Question:
    prompt: str
    secret: bool = False


@dataclass(frozen=True)
class Answer:
    value: str | None


@dataclass(frozen=True)
class Result:
    code: int


class ProcessInteraction:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    @property
    def cancelled(self) -> bool:
        return False

    @property
    def interactive(self) -> bool:
        return True

    def write(
        self, text: str, *, end: str = "\n", error: bool = False, flush: bool = False
    ) -> None:
        self.connection.send(Progress(text.rstrip("\n")))

    def _ask(self, question: Question) -> str | None:
        self.connection.send(question)
        answer = self.connection.recv()
        if not isinstance(answer, Answer):
            raise ValueError("Invalid local interaction response")
        return answer.value

    def confirm(self, prompt: str) -> bool:
        return self._ask(Question(prompt)) == "yes"

    def subscription(self) -> str:
        value = self._ask(Question("HTTPS VPN subscription (not saved or logged)", secret=True))
        if value is None:
            raise EOFError("Subscription input cancelled")
        return value


def serve(root: Path, request: InstallRequest, connection: Connection) -> None:
    """Own installer signals in a main thread; no package hooks run in the UI process."""
    os.setsid()

    def interrupted(signum: int, frame: FrameType | None) -> None:
        raise KeyboardInterrupt("Terminal installation interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        interaction = ProcessInteraction(connection)
        code = InstallationService(root, interaction=interaction).run(request)
        connection.send(Result(code))
    except (Exception, KeyboardInterrupt) as exc:
        try:
            connection.send(Progress("FAILED: " + error_text(exc)))
            connection.send(Result(5))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()
