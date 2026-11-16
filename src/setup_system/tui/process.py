"""Explicit pipe adapter; independent of Textual's captured stdout/stderr objects."""

from __future__ import annotations

import os
import selectors
import subprocess
import sys
from pathlib import Path
from typing import IO

from setup_system.application.installation_host import (
    MAX_MESSAGE_CHARACTERS,
    Answer,
    Progress,
    Question,
    Result,
    decode_message,
    encode_message,
    encode_request,
)
from setup_system.application.installer import InstallRequest
from setup_system.application.interaction import Interaction
from setup_system.diagnostics import error_text


class ProcessInstallation:
    def __init__(self, root: Path, interaction: Interaction) -> None:
        self.root = root
        self.interaction = interaction

    @staticmethod
    def _send(writer: IO[bytes], message: str) -> None:
        pending = message.encode()
        while pending:
            written = writer.write(pending)
            if not written:
                raise BrokenPipeError("Installation worker input closed")
            pending = pending[written:]
        writer.flush()

    def run(self, request: InstallRequest) -> int:
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-m", "setup_system.application.installation_host"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            bufsize=0,
        )
        pending, errors = bytearray(), bytearray()
        output_open = True
        try:
            if process.stdin is None or process.stdout is None or process.stderr is None:
                raise RuntimeError("Installation worker pipes are unavailable")
            self._send(process.stdin, encode_request(self.root, request))
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, "output")
                selector.register(process.stderr, selectors.EVENT_READ, "errors")
                while True:
                    if self.interaction.cancelled:
                        raise RuntimeError("Terminal interface closed; stopping installation")
                    if b"\n" not in pending:
                        for key, _ in selector.select(0.2):
                            chunk = os.read(key.fd, 65536)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                if key.data == "output":
                                    output_open = False
                            elif key.data == "output":
                                pending.extend(chunk)
                            elif len(errors) < 65536:
                                errors.extend(chunk[: 65536 - len(errors)])
                        if len(pending) > MAX_MESSAGE_CHARACTERS and b"\n" not in pending:
                            raise ValueError("Oversized installation worker message")
                        if b"\n" not in pending:
                            if not output_open and process.poll() is not None:
                                detail = error_text(errors.decode(errors="replace"))
                                raise RuntimeError(
                                    "Installation worker stopped without a result: " + detail
                                )
                            continue
                    raw, _, remainder = pending.partition(b"\n")
                    pending = bytearray(remainder)
                    message = decode_message(raw.decode())
                    if isinstance(message, Progress):
                        self.interaction.write(message.text)
                    elif isinstance(message, Question):
                        try:
                            answer = (
                                self.interaction.subscription()
                                if message.secret
                                else ("yes" if self.interaction.confirm(message.prompt) else None)
                            )
                        except EOFError:
                            answer = None
                        self._send(process.stdin, encode_message(Answer(answer)))
                    elif isinstance(message, Result):
                        if process.wait(timeout=5) != 0:
                            raise RuntimeError("Installation worker did not exit cleanly")
                        return message.code
                    else:
                        raise ValueError("Unexpected installation worker message")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=45)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
