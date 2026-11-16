"""Typed JSON protocol for one isolated installation operation."""

from __future__ import annotations

import json
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import TextIO, cast

from setup_system.application.installer import InstallationService, InstallRequest, Operation
from setup_system.diagnostics import error_text

MAX_MESSAGE_CHARACTERS = 1024 * 1024
PROGRESS_CHUNK_CHARACTERS = 64 * 1024


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


WireMessage = Progress | Question | Answer | Result


def _payload(raw: str) -> dict[str, object]:
    if not raw or len(raw) > MAX_MESSAGE_CHARACTERS:
        raise ValueError("Missing or oversized installation message")
    value: object = json.loads(raw)
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("Invalid installation message structure")
    return cast(dict[str, object], value)


def encode_message(message: WireMessage) -> str:
    payload: dict[str, object]
    match message:
        case Progress(text):
            payload = {"kind": "progress", "text": text}
        case Question(prompt, secret):
            payload = {"kind": "question", "prompt": prompt, "secret": secret}
        case Answer(value):
            payload = {"kind": "answer", "value": value}
        case Result(code):
            payload = {"kind": "result", "code": code}
    return json.dumps(payload) + "\n"


def decode_message(raw: str) -> WireMessage:
    data = _payload(raw)
    kind = data.get("kind")
    if kind == "progress" and set(data) == {"kind", "text"}:
        text = data["text"]
        if isinstance(text, str):
            return Progress(text)
    if kind == "question" and set(data) == {"kind", "prompt", "secret"}:
        prompt, secret = data["prompt"], data["secret"]
        if isinstance(prompt, str) and isinstance(secret, bool):
            return Question(prompt, secret)
    if kind == "answer" and set(data) == {"kind", "value"}:
        value = data["value"]
        if value is None or isinstance(value, str):
            return Answer(value)
    if kind == "result" and set(data) == {"kind", "code"}:
        code = data["code"]
        if type(code) is int and 0 <= code <= 255:
            return Result(code)
    raise ValueError("Invalid installation message fields")


def encode_request(root: Path, request: InstallRequest) -> str:
    return (
        json.dumps(
            {
                "root": str(root),
                "operation": request.operation.value,
                "user": request.user,
                "yes": request.yes,
                "overrides": {kind: str(path) for kind, path in request.overrides.items()},
                "subscription_file": str(request.subscription_file)
                if request.subscription_file
                else None,
            }
        )
        + "\n"
    )


def read_request(reader: TextIO) -> tuple[Path, InstallRequest]:
    data = _payload(reader.readline(MAX_MESSAGE_CHARACTERS + 1))
    if set(data) != {"root", "operation", "user", "yes", "overrides", "subscription_file"}:
        raise ValueError("Invalid installation request fields")
    root, operation, user = data["root"], data["operation"], data["user"]
    yes, overrides, subscription = data["yes"], data["overrides"], data["subscription_file"]
    if (
        not isinstance(root, str)
        or not Path(root).is_absolute()
        or not isinstance(operation, str)
        or operation not in {Operation.PLAN.value, Operation.APPLY.value}
        or not isinstance(yes, bool)
        or not (user is None or isinstance(user, str))
        or not (subscription is None or isinstance(subscription, str))
        or not isinstance(overrides, dict)
        or any(
            kind not in {"config", "data", "state", "cache"} or not isinstance(path, str)
            for kind, path in overrides.items()
        )
    ):
        raise ValueError("Invalid installation request values")
    return Path(root), InstallRequest(
        Operation(str(operation)),
        user=user,
        yes=yes,
        overrides={str(kind): Path(str(path)) for kind, path in overrides.items()},
        subscription_file=Path(subscription) if subscription else None,
    )


class ProcessInteraction:
    def __init__(self, reader: TextIO, writer: TextIO) -> None:
        self.reader, self.writer = reader, writer

    @property
    def cancelled(self) -> bool:
        return False

    @property
    def interactive(self) -> bool:
        return True

    def send(self, message: WireMessage) -> None:
        self.writer.write(encode_message(message))
        self.writer.flush()

    def write(
        self, text: str, *, end: str = "\n", error: bool = False, flush: bool = False
    ) -> None:
        for offset in range(0, max(1, len(text)), PROGRESS_CHUNK_CHARACTERS):
            self.send(Progress(text[offset : offset + PROGRESS_CHUNK_CHARACTERS].rstrip("\n")))

    def _ask(self, question: Question) -> str | None:
        self.send(question)
        raw = self.reader.readline(MAX_MESSAGE_CHARACTERS + 1)
        if not raw:
            raise EOFError("Installation UI closed")
        answer = decode_message(raw)
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


def main(reader: TextIO, writer: TextIO) -> int:
    """Own installer signals in the subprocess main thread with explicit streams."""
    interaction = ProcessInteraction(reader, writer)

    def interrupted(signum: int, frame: FrameType | None) -> None:
        raise KeyboardInterrupt("Terminal installation interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        root, request = read_request(reader)
        code = InstallationService(root, interaction=interaction).run(request)
        interaction.send(Result(code))
    except (Exception, KeyboardInterrupt) as exc:
        try:
            interaction.write("FAILED: " + error_text(exc))
            interaction.send(Result(5))
        except (BrokenPipeError, EOFError, OSError):
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.stdin, sys.stdout))
